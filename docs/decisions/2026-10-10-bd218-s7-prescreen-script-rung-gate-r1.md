# bd#218 s7 — validation gate r1 (spec r1 + RED)

Audited: `docs/decisions/2026-10-10-bd218-s7-prescreen-script-rung.md` (r1), `engine_py/tests/test_bd218_s7_script_rung.py` (44 tests, facts: 0 pass / 44 fail), amendment `engine_py/tests/test_bd218_s1_preflight_rung.py::test_AC7`.
Code read: `check_ladder.py` (whole), `phase_5_implement._invoke_validation_llm` (L6813-6965), `preflight.receipt_rung` (L295-310), `lib/git_cwd.py` ambient sources, pinned siblings `test_bd141_check_ladder.py` (L16/L17/L17b/L18), `test_bd218_s1_preflight_rung.py` (AC3-AC17), `test_bd218_s2_receipt_producer.py`, `bd-ladder-map.md`.
Facts package: syntax/cite/stub ok, scoped 0/44, siblings ok, no pinned/known-reds.

## Step 1 — spec-internal consistency (literal-token drift)

- `prescreen_verdict`, `preflight_receipt`, `script_rung`, `config-error`, `classifier_status`, `mode="shadow"`: consistent across §2/§3 and with the code.
- Drift 1 (MINOR-3): op1 says `red_step` "missing or not a **non-empty** string -> unknown"; AC1 says "no/**blank**/non-str". `"   "` is non-empty but blank. The RED (`test_AC1_..._unknown[blank]`) follows AC1. An implementer following op1 literally (`isinstance(s, str) and s`) yields rule `preflight:   ` and fails the test.
- Drift 2 (MINOR-4): op2 says the rung block "keeps the record it already computes in a local `_rung_rec`"; the code's local is `_pr_rec`, and it is bound only inside the non-ambient branch. That is harmless (GREEN has to add `_rung_rec = None` before the `try`), but "already computes in" names something that does not exist.

## Step 1.5 — rule-overlap simulation

The run decision at the call site: kill switch (`script_rung`) x classifier configured x `_rung_rec` status.

| script_rung | classifier | rung rec | expected (spec) | RED |
|---|---|---|---|---|
| default | no | red | 1 event reject/script, status off | AC2 |
| default | no | fresh | 1 event escalate/None | AC3 |
| default | no | stale/missing/None(ambient, off, config-error) | none, prescreen not called | AC4 |
| default | no | receipt_rung returned `status:"error"` / rung block raised | none (op2: `_rung_rec` None or status not fresh/red) | **not tested** (MINOR-2) |
| default | yes | red | 1 event reject/script, status ok, findings fed | AC5, AC7 |
| default | yes | None (ambient) | today's event, findings [] | L17/L17b (pinned) |
| "off" | no | red | none | AC6 |
| "off" | yes | red | 1 event, findings [] | AC6 |
| unknown | no | red | exactly 1 config-error event | AC6 |
| **unknown** | **yes** | any | **undefined**: "treated as off" means the classifier runs and emits today's verdict event, and op3 also adds "one config-error event". That gives either 2 events, or 1 config-error event that replaces the classifier verdict (and is the classifier spawned or not?) | **not tested** (MAJOR-2) |
| unknown | no | stale/missing/ambient | op3 text: config-error event regardless of receipt | not tested (covered by MAJOR-2 fix) |
| "shadow" / None explicit | any | red | same as default | **not tested** (MINOR-1) |

`check_ladder.prescreen` dispatch with script findings: `majors` checked before the enforce/classifier branch, so the first matching branch returns reject/script whatever the classifier status (ok/error/timeout/off). Under `mode="shadow"` the classifier still runs (not `skipped`). AC5 and AC7 match this.

## Step 2 — §2 vs §3 cross-check

- op1 -> AC1. op2 reorder -> AC9. op2 feed -> AC2/AC5. op2 run rule -> AC2/AC3/AC4. op2 classifier optional -> AC7. op2 shadow/enforce -> AC5. op2 event/extra_data -> AC2/AC3/AC5/AC8. op2 exception -> AC10. op3 -> AC6. Add-only -> AC11. Siblings -> AC12.
- op2 terminal/error path "`_rung_rec` None when ... errored": no AC4 case covers it (MINOR-2).
- op3 branch "absent / None / `"shadow"` -> on": absent is covered by AC2, but explicit `None` and `"shadow"` have no AC row or test (MINOR-1).
- op3 branch "any other value -> one config-error event" has no defined outcome when a classifier is configured (MAJOR-2).
- Error-path post-conditions (config-error event shape, gate once, result passthrough) are produced by the existing `except` at L6861-6868, which op2 reuses. Consistent with AC10.

## Step 3 — RED adequacy

- Counts: AC1 17, AC2 1, AC3 1, AC4 5, AC5 1, AC6 9, AC7 3, AC8 3, AC9 1, AC10 1, AC11 2, total 44, matching the facts.
- Fail at assert time, not collect time: modules are imported lazily, `script_findings` fails with AttributeError inside the body, and every phase-5 test fails on a forcing or behaviour assert today (no classifier means no prescreen call). None is vacuous.
- Stub-passability: real `check_ladder.prescreen` everywhere except AC10 (which patches a dependency, not the unit under test). Receipts are real (`run_engine_preflight` and `receipt_rung` on a tmp git repo). Only the LLM, the event sink and the in-function producer are replaced. OK.
- Wrong-implementation probes:
  - "always emit": caught by AC4 and AC6-off.
  - "stale is a finding": caught by AC1 `stale`/`stale-was-red` and AC4 `stale`, but see MAJOR-1: AC4 `stale` is broken.
  - "`prescreen` key added without a classifier": caught by AC2/AC3/AC6/AC10.
  - "enforce honoured": caught by AC5 (event mode and spy kwargs; `skipped` would break `classifier_status == "ok"`).
  - "block not moved, receipt read twice": caught by AC9 exact order. Under rung off / ambient / config-error, s1 AC5/AC14/AC17 spies assert `receipt_rung` is not called.
  - "kill switch ignored with classifier": caught by AC6 test 2.
- **MAJOR-1 (test defect, AC4[stale]).** `test_AC4_no_classifier_and_no_evidence_means_no_prescreen` runs the forcing `_drive(...)` first. `_drive` monkeypatches `preflight.run_engine_preflight` to `lambda *a, **k: None` for the rest of the test. Only then does `case == "stale"` call `_staged(tmp_path, "stale")`, which does `res = _pf().run_engine_preflight(...)` and then `res["exit_code"]` on `None`, raising TypeError. So AC4[stale] can never go green, whatever GREEN does. It is red today for the wrong reason and stays red after a correct implementation. (`missing` returns before the producer call, and `red` is staged before the patch, so only `stale` is affected.) Fix: stage the stale repo (and the missing repo, for symmetry) before the forcing `_drive`.

## Step 4 — reachability (§1y)

- AC2/AC3/AC5/AC7/AC8: Point = new `check_ladder.script_findings(_rung_rec)` and the `_emit_safe("prescreen_verdict", ...)` call. Host = `phase_5_implement._invoke_validation_llm`. Test-path = `_drive` -> `p5._invoke_validation_llm(ctx(git_cwd=<real tmp repo>), prev)` with a receipt staged by the real `run_engine_preflight` before the patch. Reachable.
- AC6 kill switch: Point = `cfg["prescreen"]["script_rung"]` read in the same host. Same test path. Reachable.
- AC10: Point = the existing `except` (L6861), reached through the module attribute `check_ladder.prescreen` (import at L115 is a module import, so patching works). Reachable.
- AC9: order rests on the moved block sitting between `_emit_safe("preflight_receipt")` (L6917) and `invoke_llm_subprocess` (L6918). Reachable.

## Coordinator checks

1. **Move after the preflight block.** No existing test pins prescreen-before-preflight. s1 AC8 only asserts that both events precede the LLM. `extra_data` key order changes (`prescreen` now after `preflight`), but every comparison is dict equality. The producer now runs before the classifier, which only adds receipt-write latency before the classifier spawn. Safe.
2. **Pinned tests.**
   - L16: its harness has no `git_cwd`, so the source is `cwd`, which is ambient. The rung is ambient-skipped, `_rung_rec` is None, the pre-screen does not run and `extra_data` is unchanged. Byte-for-byte holds for all 7 cfgs, including `"x"`, `[]`, `None`, `{}`, `{"timeout_s": 5}` and `{"classifier_cmd": None}`.
   - L17/L17b: ambient, so `findings == []` and the payload findings stay `[]`. Holds.
   - L18: `classifier_cmd: "not-a-list"` makes `_validate` raise, giving config-error. Holds.
   - s1 AC5: mode off means no record and no pre-screen. In default mode the spy returns fresh, so an escalate event appears; AC5 asserts only rung events and kwargs. Holds.
   - s1 AC11: no classifier, so kwargs are unchanged. Holds.
   - s1 AC14: ambient-skip, so no pre-screen. Holds.
   - s2 AC9: no `prescreen` key without a classifier. Holds.
   - No AC contradicts a pinned test.
3. **When the pre-screen runs.** The rule is sound. Ambient harnesses are excluded through `_rung_rec = None`. Gaps: a receipt_rung `status:"error"` record is untested (MINOR-2), and unknown kill-switch value x classifier is undefined (MAJOR-2).
4. **Config-error shape reuse.** Exception path: identical shape to L17b(b) (`_assert_config_error` is copied verbatim). The kill-switch typo reuses the same shape. That is acceptable, but it overloads "the prescreen call failed" with "config typo", and in the classifier case it collides with the "exactly one `prescreen_verdict` per call" invariant from L17 (MAJOR-2). Also new: `prescreen.timeout_s` invalid (e.g. 0) plus no classifier plus a red receipt now gives a config-error event instead of the script verdict. Today that config is silent. This is defined by op2's "any exception -> config-error" but not called out (MINOR-6).
5. **Class + chokepoint.** SYSTEMATIC is fine. The chokepoint `script_findings` owns the mapping. However, the "only fresh/red carry information" rule now lives in three places: `script_findings` (stale -> []), the phase-5 run rule (`status in ("fresh","red")`) and `reject_stats.ladder_table` `informative`. MINOR-5: expose one predicate or constant (e.g. `check_ladder.SCRIPT_EVIDENCE_STATUSES`) and use it at the call site, so the call site truly "adds no mapping of its own".
6. **Scope dropped silently.** None. The out-of-scope list matches ladder-map rollout steps 2-5, the enforce/skip variant is explicitly PAUSED by the map, and preflight/reject_log/reject_stats are untouched.
7. **s1 AC7 amendment.** Justified: the old assertion "no `prescreen_verdict` with no classifier" is exactly the behaviour s7 changes. The new form is `len(verdicts) <= 1`, `all(status == "off")` and gate once, which keeps the sibling green before and after GREEN. The exact count is pinned by s7 AC3. Slightly weaker than the spec wording: "no classifier process started" is only inferred from status `off` and is not asserted (MINOR-7). Optional hardening: spy `check_ladder.run_classifier` and assert zero calls.
8. **Pass-with-wrong-impl.** See Step 3. The remaining holes are MINOR-1 (an implementation rejecting `"shadow"` as a typo passes), MINOR-2 (an implementation gating on `status not in (stale, missing)` emits for `error` and passes) and MAJOR-2.

## Adversarial edges (not covered by the §3 AC table)

1. Explicit `{"script_rung": "shadow"}` and `{"script_rung": None}`, which op3 says behave as default. No test, so a typo-strict implementation passes (MINOR-1).
2. `receipt_rung` returning `{"status": "error"}` (verify_receipt raising inside it), and the rung block raising (s1 AC4 path). Expect no pre-screen and no event (MINOR-2).
3. Unknown `script_rung` value with a classifier configured: event count and whether the classifier spawns (MAJOR-2).
4. Unknown `script_rung` value with an ambient / stale / missing rung: op3 says one config-error event regardless. Untested.
5. `prescreen.timeout_s` invalid with no classifier and a red receipt: a script verdict is lost to config-error (MINOR-6).
6. Decoy fence: a stale receipt whose stored `red_step` is set (stale-was-red) at the phase-5 level, not only in `script_findings`. AC4 `stale` uses a RED_SRC repo, so its stale receipt has `red_step` None, and an implementation keyed on `red_step is not None` would not be caught by AC4. AC1 `stale-was-red` covers only the pure function.

## Findings

1. **MAJOR-1 (RED defect, AC4[stale]):** the stale repo is staged after `_drive` has patched `run_engine_preflight` to return None, so `_staged` raises TypeError. The test can never pass. Stage all case repos before the forcing `_drive`.
2. **MAJOR-2 (spec gap, op3/AC6, rule overlap):** unknown `script_rung` value with a classifier configured has no defined outcome. Is it 2 events (classifier verdict plus config-error), or 1 config-error event, and does the classifier spawn? This breaks L17's "exactly one `prescreen_verdict` per call" invariant in one reading. Define it in op3 and AC6 (recommended: exactly one config-error event, classifier not spawned, findings irrelevant) and add a RED param.
3. MINOR-1: op3 values `None` / `"shadow"` are untested. Add two params to an AC6 default-behaviour test.
4. MINOR-2: no phase-5 case for a receipt_rung `error` record or the rung-block exception. Add an AC4 param.
5. MINOR-3: op1 "non-empty" vs AC1 "blank". Align op1 to "non-blank (after strip)".
6. MINOR-4: `_rung_rec` vs the existing `_pr_rec` wording in op2.
7. MINOR-5: the fresh/red evidence rule is duplicated at the call site. Expose a single predicate or constant from `check_ladder`.
8. MINOR-6: an invalid `timeout_s` without a classifier now suppresses the script verdict (config-error). Either pass `DEFAULT_TIMEOUT_S` when `classifier_cmd is None`, or state it in the spec.
9. MINOR-7: the s1 AC7 amendment infers "no classifier process" from status `off`. An optional `run_classifier` spy would make it explicit.
10. Adversarial edge 6 (stale-was-red at the phase-5 level) is advisory: add a STUB_SRC stale repo to AC4.

## Checklist

- AC1: PASS
- AC2: PASS
- AC3: PASS
- AC4: FAIL
- AC5: PASS
- AC6: FAIL
- AC7: PASS
- AC8: PASS
- AC9: PASS
- AC10: PASS
- AC11: PASS
- AC12: PASS


VERDICT: REJECT
