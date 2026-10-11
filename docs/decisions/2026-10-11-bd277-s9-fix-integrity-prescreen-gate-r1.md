# bd#277 (bd#218 s9): validation gate r1 (spec r1 + RED)

Audited: `docs/decisions/2026-10-11-bd277-s9-fix-integrity-prescreen.md`, `engine_py/tests/test_bd218_s9_fix_integrity_prescreen.py` (33 tests; preflight facts: 0 pass / 33 fail on 31adb25, all 15 siblings pass).
Code read: `phase_6_fix_integrity.py` L590-685 (`_invoke_fix_integrity_llm`, the s6 receipt block, `_attempt`, `reroll_until_verdict` call), `phase_5_integrity.py` L483-570 (merged s8 block), the s8 gate r2 for format.

## Step 1: spec-internal consistency (literal-token drift)

- `gate`: spec uses `fix_integrity` (underscore) everywhere in its own text; code has `"gate": "fix_integrity"` (L628) and `gate_label="fix_integrity"` (L673); RED `_assert_reject` / `_assert_config_error` assert `"fix_integrity"`. The only hyphen form is the issue wording, which the spec explicitly overrides. Consistent.
- `phase 6`: code L627 `"phase": 6`; spec items 6-7; RED asserts `phase == 6`. Consistent.
- `cycle`: code L628 `cfg.get("cycle", 1)`; spec item 6 `cfg.get("cycle", 1)`; RED passes `cycle=3` through `org_config` (`_drive(**org)` -> `_ctx`), not through `prev.data`. Consistent, and different from s8 (`prev.data.get("cycle", 1)`), see straight-copy check.
- `prescreen_verdict`, `preflight_receipt`, `script_rung`, `classifier_status`, `config-error`, `_rung_rec`: identical in spec, RED (`EVENT_KEYS` equals spec item 6, 11 keys) and the s8 block.
- Spec header: Expiry 2026-10-25, same as s8; the s7 date 2026-10-24 is mentioned once as the pre-GREEN block's own expiry (the N2 wording from s8 recurs, advisory, MINOR-4).
- Spec line numbers (l.68 import, l.624-660) match the code (import at L67-68 region, block L620-659). Fine.

## Step 1.5: rule-overlap simulation

Decision order (items 2-5, 9): `_rung_rec` set only when `receipt_rung` returned; flag None/"shadow" on, "off" off, other bad; non-dict `prescreen` default; act iff bad flag, or on and status in {fresh, red}; `classifier_cmd=None`, `mode="shadow"` always.

| script_rung | preflight_rung | receipt | classifier | prescreen.mode | first-matching branch, outcome | RED |
|---|---|---|---|---|---|---|
| default / shadow / None / non-dict | default | red/stub | none | any | act; reject/script, clf off, `gate fix_integrity`, cycle from cfg | AC1 (cycle 1 and 3), AC4 x5, AC8, AC9, AC10 |
| default | default | red/stub | set | enforce | act; classifier None, mode shadow; reject, no spawn | AC5 first |
| default | default | fresh | none | any | act; escalate/None | AC2 |
| default | produce False | stale, stale-red, missing | none or set | any | no act | AC3 x3, AC5 x3 |
| default | default | receipt_rung raises / ambient / mode off / mode bogus | none | any | `_rung_rec` None; no act | AC3 x4 |
| default | default | ambient | set | any | no act, no spawn | AC5 ambient |
| "off" | default | red | none or set | any | flag off; no act, no spawn | AC4 off, AC5 flag-off, AC9 |
| "bogus" / 7 | default or mode off | red | none | any | one config-error event, no prescreen | AC4 x4 |
| "bogus" | default | red | set | any | one config-error event, no spawn | AC5 bad flag |
| default | default | red | none | prescreen / script_findings raise | config-error event, result passes through | AC6 x2 |
| default | default | verdict_override | none | any | return before the block; no receipt, no event, no model | AC3 override |
| "bogus" | default | verdict_override | none | any | spec item 10: no event | not tested (adversarial 1) |

Every tested row yields the spec outcome at the first matching branch. In `check_ladder.prescreen` the no-classifier check precedes the enforce/skip branch, so a red receipt is always reject/script and a fresh one escalate/None in shadow without a classifier (confirmed by s8 r2 walk, same function).

## Step 2: section 2 (design) vs section 3 (AC) cross-check

- Item 1 -> AC3, AC1. Item 2 -> AC4. Item 3 -> AC2, AC3, AC4, AC5. Item 4 -> AC4, AC5 bad flag, AC6. Item 5 -> AC5 (classifier None, mode shadow asserted on the recorded kwargs). Items 6-7 -> AC1 (incl. cycle 3), AC2, AC4, AC5 bad flag. Item 8 -> AC8, AC1, AC9. Item 9 -> AC3 rung-off, AC4 rung-off x2. Item 10 -> AC3 override. Once per call -> AC10. Billing -> AC7. Siblings -> AC11.
- Every terminal or error branch in the design (config error, raise, override short-circuit, no evidence) has an AC and a test. No orphan AC.
- Note: AC5's spec wording lists all cases; consistent with the tests (no N7-style lag).

## Step 3: RED adequacy

- Count: AC1 1, AC2 1, AC3 8, AC4 1+5+4 = 10, AC5 1+1+4+1 = 7, AC6 2, AC7 1, AC8 1, AC9 1, AC10 1 = 33; matches facts.
- Assert time, not collect time: all module imports lazy; today `phase_6_fix_integrity` never reaches `check_ladder`, so forcing asserts (`len(seen) == 1`, `_verdicts` count) fail. Facts: 33 fail, none at collection.
- Not vacuous: `check_ladder.prescreen` is a recording spy delegating to the real function; `script_findings` real; patched only in AC6 (dependency of the UUT, not the UUT). Receipts from the real producer in real tmp git repos; stale/stale-red/missing staged via `run_engine_preflight(..., phase="green")` plus `produce: False`. Only `invoke_llm_subprocess` and `_emit_safe` are replaced, as in the s6 tests.
- Correct-GREEN walk: AC1 (red/stub reject; cycle 1 and 3 via cfg; `extra_data` has no `prescreen`, `preflight` equals s6 value) passes. AC2 passes. AC3 8 cases pass (forcing run before staging overrides; `receipt_rung` patched as module attribute, code calls `preflight.receipt_rung`; override returns at L598 before everything; `result.data["skipped"]` is set there). AC4 all 10 pass. AC5 four groups pass (marker absent because `classifier_cmd=None`; kwargs read by keyword matches the spec call shape). AC6: `script_findings` raising is hit while evaluating the argument, so config-error; result passes through. AC7: kwargs contain no repo path; equal. AC8: order from one shared `order` list, `preflight_receipt` then `prescreen_verdict` then model; passes. AC9: kwargs equality between on and off runs (same repo, re-produced receipt); passes. AC10: default re-roll budget 1 (env var cleared); block outside `_attempt` gives one event.
- Test-harness check (cycle): `_drive` builds `org_config` from `**org`, so `cycle=3` reaches `cfg`. `prev.data` has no `cycle` key. Correct GREEN reading `cfg.get("cycle", 1)` passes; the second repo goes under `tmp_path/"c3"`, no collision.
- Straight-copy check (lazy GREEN copies the merged s8 block, changing nothing but the `gate` and `phase` strings and inserting `_rung_rec`): its cycle comes from `prev.data.get("cycle", 1)` = 1, so `test_AC1` cycle-3 half fails on `ev["cycle"] == 3`. Copying the s7 block (classifier run rule) fails the AC5 groups as in s8 r2. A GREEN that hard-codes `gate "integrity"` or `phase 5` fails every event assert. A GREEN that keeps cycle from `prev.data` but fixes gate and phase is caught by AC1 only (one assertion, enough). Verdict: a lazy copy of s8 fails where it should.
- Wrong-implementation probes caught: block inside `_attempt` (AC10); event after the model call (AC8); `prescreen` key in `extra_data` (AC1, 2, 4, 5, 6, 9); stale counted as evidence (AC3, AC5); enforce honoured / classifier spawned (AC5); config error also calling `prescreen` (AC4, AC5); `"shadow"` / `None` rejected (AC4); block before the override return with a default flag is not distinguishable, but the s6 receipt block sits after the override return, so any block placed after it inherits item 10.
- Not caught (advisory): see adversarial edges.

## Step 4: reachability (section 1y)

- AC1, 2, 5, 7, 8, 9, 10. Point: the new `check_ladder.prescreen(...)` call and `_emit_safe("prescreen_verdict", ...)` between L659 and `_attempt` (L661). Host: `phase_6_fix_integrity._invoke_fix_integrity_llm`. Test path: `_drive` -> `_invoke_fix_integrity_llm(_ctx(git_cwd=<real tmp repo>), _prev)`. Reachable.
- AC3 no-evidence / override. Point: the non-acting path and the L598 early return. Host: same function. Test path: pre-staged repos with `produce: False`, no `git_cwd` (ambient), patched `receipt_rung`, `verdict_override` in `prev.data`. Reachable.
- AC4 / AC5 bad flag. Point: flag read and config-error event, outside the `mode == "off"` guard at L625. Host: same. Test path: `org_config["prescreen"]`. Reachable.
- AC6. Point: the new try/except. Host: same. Test path: module attributes of `check_ladder`. Reachable.

## (1)-(5) of the request, directly

1. Passable by a correct GREEN per items 1-10 and not vacuous: yes, every test (walk above).
2. Gate value `fix_integrity`, phase 6, cycle from cfg: consistent across spec, RED and code (L627-628, L673).
3. `verdict_override` staging: correct. `prev_extra={"verdict_override": "NO_CHANGES"}` on a red repo with default config; asserts no event, no prescreen call, no model call, `skipped is True`. The forcing run in the same test (red repo) proves the spy is live. Weakness only with a bad flag (adversarial 1).
4. Sibling list (§1a): complete for the tests that run the step with a repo or the ladder. One omission: `engine_py/tests/test_bd84_model_output_normalizer.py` calls `_invoke_fix_integrity_llm` 9 times (L492-641) and is not in `sibling_tests`. Its ctx has `org_config = {"scratchpad_dir": ...}` only (no `git_cwd`), so the cwd is ambient, `_rung_rec` stays None and no event is emitted: cannot be affected. MINOR-1 (list vs claim), same as s8's N3.
5. Gaps: adversarial edges below, all advisory.

## Adversarial edges (not covered by the section 3 AC table)

1. **`verdict_override` plus a bad `script_rung`** (`"bogus"`): item 10 says nothing is emitted, including no config-error event; the override test uses only the default flag, so a block placed before the L598 early return would only be caught if it emits on a bad flag. No test.
2. **`receipt_rung` returning a non-raising odd record** (non-dict, `status: "error"`, no `red_step`): `_rung_rec` handling is unspecified beyond "set after return". No test.
3. **Decoy cycle sources**: `prev.data["cycle"]` set to a different value than `org_config["cycle"]` (the s8 source vs the s9 source). Today the AC1 cycle-3 half catches a pure s8 copy only because `prev.data` has no `cycle`; with a decoy present a GREEN reading both would be pinned. Also a non-int `cycle` in cfg. No test.
4. **Invalid `prescreen.timeout_s`** (0, negative, string) with a red receipt: spec hard-codes the default, so the event must still be a normal reject; a copy of s7 that reads it gives a config-error event. No test.
5. **Flag variants** `"OFF"`, `" off "`, `True`, `0`: all "any other value" -> one config-error event; only `"bogus"` and `7` tested. Low risk.
6. **`preflight_rung.produce: "x"` (non-bool) with default `script_rung`**: s6 config-error path leaves `_rung_rec` None, so no `prescreen_verdict`; AC3 covers `mode: "bogus"` but not this config-error variant. Low risk (same branch outcome).

## Findings

1. MINOR-1: `test_bd84_model_output_normalizer.py` runs `_invoke_fix_integrity_llm` and is missing from `sibling_tests` (cannot be affected: ambient cwd, no rung). Advisory; add or note the grep-vs-list difference.
2. MINOR-2: no test for `verdict_override` plus a bad flag (adversarial 1). Advisory.
3. MINOR-3: no decoy test for the cycle source, no test for an invalid `timeout_s` or a non-dict receipt record (adversarial 2-4). Advisory.
4. MINOR-4: expiry date wording 2026-10-25 vs s7's 2026-10-24 for the shared flag. Advisory.

No MAJOR. Every AC has at least one failing assertion at assert time, no test is vacuous, and none can fail a correct implementation.

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
