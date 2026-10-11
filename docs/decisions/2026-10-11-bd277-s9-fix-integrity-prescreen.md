---
red_tests:
  - engine_py/tests/test_bd218_s9_fix_integrity_prescreen.py
sibling_tests:
  - engine_py/tests/test_bd218_s8_integrity_prescreen.py
  - engine_py/tests/test_bd218_s7_script_rung.py
  - engine_py/tests/test_bd218_s5_green_receipt.py
  - engine_py/tests/test_bd218_s1_preflight_rung.py
  - engine_py/tests/test_bd218_s2_receipt_producer.py
  - engine_py/tests/test_bd219_s6_fix_green_receipt.py
  - engine_py/tests/test_bd141_check_ladder.py
  - engine_py/tests/test_bd164_preflight.py
  - engine_py/tests/test_phase_6_fix_integrity.py
  - engine_py/tests/test_GH1399_advisory_format_terminal.py
  - engine_py/tests/test_bd141_p4d_role_template_injections.py
  - engine_py/tests/test_bd119_role_template.py
  - engine_py/tests/test_bd94_engine_owned_paths.py
  - engine_py/tests/test_gh886_fix_tail_autocommit.py
  - engine_py/tests/test_phase6_bounded_run_7D2BAD22.py
  - engine_py/tests/test_bd84_model_output_normalizer.py
paths:
  - engine_py/bytedigger_engine/workflows/phase_6_fix_integrity.py
  - CHANGELOG.md
---
# bd#277 (bd#218 s9): the fix-integrity gate gets a shadow pre-screen verdict with the script rung

**Status: r1, TIER2.** Slice s9 of #218 (merged so far: s1 #228, s2 #230, s3 #233, s5 #235, s6 #238, s7 #251, s8 #276).
**Class:** SYSTEMATIC: a gate that already holds a deterministic receipt but never hands it to the check ladder, so there is no shadow evidence of how often the script rung alone would have rejected what the model is then asked to judge. Same class as s8; the third gate to get the measuring point.
**Chokepoint:** `phase_6_fix_integrity._invoke_fix_integrity_llm`, the one function that computes the phase-green receipt and calls the fix-integrity model. The mapping receipt to findings stays in `check_ladder.script_findings` (reused as is, not touched); the call site adds no mapping of its own.
**Side of the seam:** engine. **Owner:** bd-ladder lot. **Expires:** 2026-10-25 (+14 d), the same date as s8: by then the integrity and fix-integrity verdicts have shadow evidence and one keep / enforce / remove decision (s7's pre-GREEN block expires 2026-10-24).

| | |
|---|---|
| **LLM calls added** | None. A receipt read, a pure mapping, one event. No classifier call, no key or environment read, no test run. |

## Principles (Guy's, for bd)

1. **Deterministic first: script, then a cheap check, then the model.** The rung is the receipt the engine already wrote; the model gate stays the only approver.
2. **Provider-agnostic: a path degrades, it does not fail.** Any failure of the rung or of `prescreen` becomes one config-error event; the gate still runs once with the same arguments.
3. **Works on a subscription and on API tokens.** Nothing here reads a credential (AC7 runs the same case under three environments).
4. **Shadow first, enforce later.** `mode="shadow"` is hard-coded; a reject is recorded and changes nothing. Kill switch, owner and expiry above.

## Name of the gate (code wins over the issue)

The issue writes `gate: fix-integrity`. The code names this gate `fix_integrity` with an underscore, in the same function: `"gate": "fix_integrity"` on the `preflight_receipt` event and `gate_label="fix_integrity"` on the model call. This slice follows the code: the new event carries **`gate: "fix_integrity"`**, so that the receipt event and the verdict event of one gate carry the same value and can be joined. (The integrity gate's `integrity` has no such ambiguity.) Noted in the PR.

## Problem (measured on 31adb25)

`phase_6_fix_integrity.py` imports `preflight` (l.68) and in `_invoke_fix_integrity_llm` writes the phase-green receipt (s6), reads `preflight.receipt_rung("green", tree)` into `_pr_rec`, emits `preflight_receipt` (`gate: fix_integrity`, `phase: 6`) and puts `{"status", "red_step"}` into the model call's `extra_data["preflight"]` (l.624-660). The file does not import or call `check_ladder`, so no `prescreen_verdict` is ever emitted by the fix-integrity gate. The pre-GREEN gate has done this since s7 (`phase_5_implement.py`), the integrity gate since s8 (`phase_5_integrity.py`).

## Design (SHADOW, ADD-ONLY)

**The fix-integrity model call runs exactly once per step call (before any completeness re-roll), with the same arguments whatever the pre-screen says. The verdict is recorded and never read back.**

One new block in `_invoke_fix_integrity_llm`, directly after the s6 receipt block and before `_attempt`. Computed once per step call (not per re-roll attempt). A new module import `from bytedigger_engine import check_ladder`.

1. The receipt block keeps the record it already computes in a local `_rung_rec` (initialised `None`; set right after `receipt_rung` returns; stays `None` when the rung is off, ambient-skipped, config-error, or raised).
2. Flag `org_config["prescreen"]["script_rung"]`, shared with s7 and s8: absent / `None` / `"shadow"` is on; `"off"` is off; any other value (or a non-str) is a config error; a non-dict `prescreen` value is the default (on).
3. The block acts when the flag is a config error, **or** the flag is on and `_rung_rec["status"]` is `"fresh"` or `"red"`. Otherwise nothing is called or emitted.
4. Config error: exactly one `prescreen_verdict` event in the config-error shape (below); `check_ladder.prescreen` is not called.
5. Otherwise: `check_ladder.prescreen(findings=check_ladder.script_findings(_rung_rec), gate_input=prev.data["prompt"], classifier_cmd=None, mode="shadow", timeout_s=check_ladder.DEFAULT_TIMEOUT_S)`. `classifier_cmd` is always `None`: `prescreen.classifier_cmd` is not read, no process is spawned, whatever the config says.
6. One `prescreen_verdict` event, keys: `outcome`, `rung`, `mode`, `classifier_status`, `classifier_label`, `classifier_confidence`, `classifier_ms`, `cost_usd`, `cycle`, `phase` (6), and **`gate`: `"fix_integrity"`**. `cycle` is `cfg.get("cycle", 1)`, the value this gate's `preflight_receipt` event already carries (the fix-integrity step data has no `cycle` key, unlike the integrity step's). Red receipt: `outcome "reject"`, `rung "script"`, `mode "shadow"`, `classifier_status "off"`. Fresh: `outcome "escalate"`, `rung None`.
7. Config-error shape: `outcome "escalate"`, `rung None`, `mode "shadow"`, `classifier_status "config-error"`, the other classifier fields `None`, `cycle`, `phase 6`, `gate "fix_integrity"`. Also used when `prescreen` (or anything else in the block) raises.
8. Emit order: `preflight_receipt`, `prescreen_verdict`, then the model call. `extra_data` of the model call is not changed by this block (no `prescreen` key); the s6 `extra_data["preflight"]` stays as is.
9. `preflight_rung.mode = "off"` leaves `_rung_rec = None`, so no rung and no event; a typo in `script_rung` still gives its one config-error event (same as s7 and s8).
10. The `verdict_override` short-circuit (equal SHAs or empty diff) returns before the receipt block today and stays so: no receipt, no verdict.

The s7 event (pre-GREEN gate) and the s8 event (integrity gate) are not touched: their key sets are pinned by `test_bd218_s7_script_rung.py` and `test_bd218_s8_integrity_prescreen.py`.

### Out of scope (one PR, one problem)
`phase_5_integrity.py`, enforce mode, skipping or replacing the gate, the classifier rung, the review / satisfaction / CI-red gates, `check_ladder.py`, `preflight.py`, `conformance/harness.py`, the key sets of the s7 and s8 events.

## Acceptance criteria

All through the real `_invoke_fix_integrity_llm` with real tmp git repos and real receipts (the real producer runs, as in the s6 tests); only the model call and the event sink are replaced. `check_ladder.prescreen` is the real function except in AC6.

- **AC1 (reference case, real side effect)** Default config, non-ambient repo whose green receipt is red with `red_step == "stub"`: exactly one `prescreen_verdict` event with exactly the keys of item 6; `outcome "reject"`, `rung "script"`, `mode "shadow"`, `gate "fix_integrity"`, `classifier_status "off"`, `cycle 1`, `phase 6`; the model call runs once; its `extra_data` has no `prescreen` key and has `preflight == {"status": "red", "red_step": "stub"}`. With `cycle: 3` in the config the event's `cycle` is 3.
- **AC2** Fresh green receipt: one event, `escalate`, `rung None`, `gate "fix_integrity"`, `classifier_status "off"`; model once.
- **AC3 (no evidence, no event)** Receipt stale (a stale receipt that was red included), missing, `receipt_rung` raising, ambient cwd, `preflight_rung.mode: "off"`, `preflight_rung` config-error, and the `verdict_override` short-circuit: no `prescreen_verdict`, `check_ladder.prescreen` not called, model called once (not at all for the short-circuit). A forcing run in the same test shows the spy is live (a red receipt does call `prescreen`).
- **AC4 (kill switch)** `{"script_rung": "off"}`: no event. `"shadow"`, `None`, and a non-dict `prescreen` (`"x"`, `[]`, `None`): behave as the default. `"bogus"`, `7`: exactly one config-error event, `prescreen` not called, model once; the same with `preflight_rung.mode: "off"` (still one event).
- **AC5 (classifier never used)** `prescreen.classifier_cmd` set to a real script that records a marker file when run: the marker is absent in each of these cases: red receipt (`classifier_status "off"`, `extra_data` without `prescreen`, and with `prescreen.mode: "enforce"` the event `mode` is still `"shadow"`), `script_rung: "off"` (no event), no evidence (stale, stale-red, missing, ambient: no event), invalid `script_rung` (one config-error event). The model runs once in each.
- **AC6 (degrade)** `check_ladder.prescreen` patched to raise: one config-error event, model once, the step result is the one the model call produced (passed through). `script_findings` patched to raise: same.
- **AC7 (both billing modes)** The same red-receipt run under an API-key environment (`ANTHROPIC_API_KEY` set, no OAuth token), a subscription environment (`CLAUDE_CODE_OAUTH_TOKEN` set, no API key) and one with neither: identical events and identical model-call kwargs.
- **AC8 (order)** `preflight_receipt`, then `prescreen_verdict`, then the model call.
- **AC9 (add-only)** The model-call kwargs (prompt, model, timeout, tools, `hard_gate`, `gate_label`, `injections`, `extra_data`) of a red-receipt run equal those of the same run with `{"script_rung": "off"}`; the model is called once in both.
- **AC10 (once per step call)** The first model reply has no standalone VERDICT, so the existing completeness re-roll calls the model a second time: still exactly one `prescreen_verdict`.
- **AC11** Siblings pass unchanged (the files in `sibling_tests`); the s7 and s8 tests and their event key sets are untouched. No existing test is amended.

## Files

In scope: `engine_py/bytedigger_engine/workflows/phase_6_fix_integrity.py` (import, `_rung_rec`, the new block), new `engine_py/tests/test_bd218_s9_fix_integrity_prescreen.py`, `CHANGELOG.md`, this spec and its gate files.
Not in scope: every other file, in particular `check_ladder.py`, `preflight.py`, `conformance/harness.py`, `phase_5_implement.py`, `phase_5_integrity.py`, `error_codes.py` (no new error code), `flags_catalog.py` (s7 and s8 registered no catalog entry for the same flag; the owner and expiry above are the lint-visible record).
Sibling-test audit (§1a) done before this spec: `grep -l '_invoke_fix_integrity_llm\|phase_6_fix_integrity\|check_ladder\|prescreen\|preflight_receipt' engine_py/tests` gives the candidate set; the list above is the part that runs the fix-integrity step, the receipt or the ladder. None asserts the absence of a `prescreen_verdict` from the fix-integrity gate (the s1 AC7 amendment concerned the pre-GREEN gate only; `test_bd166_one_sided_predicate` bans `check_ladder` only inside `phase_5_implement._one_sided_predicate_hits`).

## Provenance (Guy 2026-10-03)

introduced: the fix-integrity gate was wired to the receipt in s6 (#238) and never to the ladder; nothing is removed or simplified. The model stage of the gate is kept: this step only adds the shadow measuring point, the evidence for a later keep / enforce / remove decision.

## LLM stages in this step

None added. The one LLM stage touched, the fix-integrity gate, is kept as is.
