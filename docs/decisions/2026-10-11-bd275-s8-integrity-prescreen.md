---
red_tests:
  - engine_py/tests/test_bd218_s8_integrity_prescreen.py
sibling_tests:
  - engine_py/tests/test_bd218_s7_script_rung.py
  - engine_py/tests/test_bd218_s5_green_receipt.py
  - engine_py/tests/test_bd218_s1_preflight_rung.py
  - engine_py/tests/test_bd218_s2_receipt_producer.py
  - engine_py/tests/test_bd219_s6_fix_green_receipt.py
  - engine_py/tests/test_bd141_check_ladder.py
  - engine_py/tests/test_bd164_preflight.py
  - engine_py/tests/test_GH786_integrity_completeness_gate.py
  - engine_py/tests/test_GH1399_advisory_format_terminal.py
  - engine_py/tests/test_gh705_callsite_stable_prefix.py
  - engine_py/tests/test_bd141_p4d_role_template_injections.py
paths:
  - engine_py/bytedigger_engine/workflows/phase_5_integrity.py
  - CHANGELOG.md
---
# bd#275 (bd#218 s8): the integrity gate gets a shadow pre-screen verdict with the script rung

**Status: r1, TIER2.** Slice s8 of #218 (merged so far: s1 #228, s2 #230, s3 #233, s5 #235, s6 #238, s7 #251).
**Class:** SYSTEMATIC: a gate that already holds a deterministic receipt but never hands it to the check ladder, so there is no shadow evidence of how often the script rung alone would have rejected what the model is then asked to judge.
**Chokepoint:** `phase_5_integrity._invoke_integrity_llm`, the one function that computes the phase-green receipt and calls the integrity model. The mapping receipt to findings stays in `check_ladder.script_findings` (reused as is, not touched); the call site adds no mapping of its own.
**Side of the seam:** engine. **Owner:** bd-ladder lot. **Expires:** 2026-10-25 (+14 d): by then the integrity verdict has shadow evidence and a keep / enforce / remove decision (same lot as s7, whose flag expires 2026-10-24).

| | |
|---|---|
| **LLM calls added** | None. A receipt read, a pure mapping, one event. No classifier call, no key or environment read, no test run. |

## Principles (Guy's, for bd)

1. **Deterministic first: script, then a cheap check, then the model.** The rung is the receipt the engine already wrote; the model gate stays the only approver.
2. **Provider-agnostic: a path degrades, it does not fail.** Any failure of the rung or of `prescreen` becomes one config-error event; the gate still runs once with the same arguments.
3. **Works on a subscription and on API tokens.** Nothing here reads a credential (AC7 runs the same case under three environments).
4. **Shadow first, enforce later.** `mode="shadow"` is hard-coded; a reject is recorded and changes nothing. Kill switch, owner and expiry above.

## Problem (measured on 76cbac3)

`phase_5_integrity.py` lines 484-523 write the phase-green receipt (s5), read `preflight.receipt_rung("green", tree)` into `_pr_rec`, emit `preflight_receipt` (`gate: integrity`) and put `{"status", "red_step"}` into the model call's `extra_data["preflight"]`. The file does not import or call `check_ladder`, so no `prescreen_verdict` is ever emitted by the integrity gate. The pre-GREEN gate has done this since s7 (`phase_5_implement.py` l.6975-7025).

## Design (SHADOW, ADD-ONLY)

**The integrity model call runs exactly once per step call, with the same arguments whatever the pre-screen says. The verdict is recorded and never read back.**

One new block in `_invoke_integrity_llm`, directly after the s5 receipt block and before `_attempt`. Computed once per step call (not per re-roll attempt). A new module import `from bytedigger_engine import check_ladder`.

1. The receipt block keeps the record it already computes in a local `_rung_rec` (initialised `None`; set right after `receipt_rung` returns; stays `None` when the rung is off, ambient-skipped, config-error, or raised).
2. Flag `org_config["prescreen"]["script_rung"]`, shared with s7: absent / `None` / `"shadow"` is on; `"off"` is off; any other value (or a non-str) is a config error; a non-dict `prescreen` value is the default (on).
3. The block acts when the flag is a config error, **or** the flag is on and `_rung_rec["status"]` is `"fresh"` or `"red"`. Otherwise nothing is called or emitted.
4. Config error: exactly one `prescreen_verdict` event in the config-error shape (below); `check_ladder.prescreen` is not called.
5. Otherwise: `check_ladder.prescreen(findings=check_ladder.script_findings(_rung_rec), gate_input=prev.data["prompt"], classifier_cmd=None, mode="shadow", timeout_s=check_ladder.DEFAULT_TIMEOUT_S)`. `classifier_cmd` is always `None`: `prescreen.classifier_cmd` is not read, no process is spawned, whatever the config says (a classifier at the integrity gate is a separate, later decision).
6. One `prescreen_verdict` event, keys: `outcome`, `rung`, `mode`, `classifier_status`, `classifier_label`, `classifier_confidence`, `classifier_ms`, `cost_usd`, `cycle` (from `prev.data`, default 1), `phase` (5), and **`gate`: `"integrity"`**. Red receipt: `outcome "reject"`, `rung "script"`, `mode "shadow"`, `classifier_status "off"`. Fresh: `outcome "escalate"`, `rung None`.
7. Config-error shape: `outcome "escalate"`, `rung None`, `mode "shadow"`, `classifier_status "config-error"`, the other classifier fields `None`, `cycle`, `phase 5`, `gate "integrity"`. Also used when `prescreen` (or anything else in the block) raises.
8. Emit order: `preflight_receipt`, `prescreen_verdict`, then the model call. `extra_data` of the model call is not changed by this block (no `prescreen` key); the s5 `extra_data["preflight"]` stays as is.
9. `preflight_rung.mode = "off"` leaves `_rung_rec = None`, so no rung and no event; a typo in `script_rung` still gives its one config-error event (same as s7).

The s7 event (pre-GREEN gate) is not touched: its key set is pinned by `test_bd218_s7_script_rung.py` and stays without `gate`.

### Out of scope (one PR, one problem)
fix-integrity (a mirror slice afterwards), enforce mode, skipping or replacing the gate, the classifier rung, the review / satisfaction / CI-red gates, `check_ladder.py`, `preflight.py`, `conformance/harness.py`, the s7 event's key set.

## Acceptance criteria

All through the real `_invoke_integrity_llm` with real tmp git repos and real receipts (the real producer runs, as in the s5 tests); only the model call and the event sink are replaced. `check_ladder.prescreen` is the real function except in AC6.

- **AC1 (reference case, real side effect)** Default config, non-ambient repo whose green receipt is red with `red_step == "stub"`: exactly one `prescreen_verdict` event with exactly the keys of item 6; `outcome "reject"`, `rung "script"`, `mode "shadow"`, `gate "integrity"`, `classifier_status "off"`, `cycle 1`, `phase 5`; the model call runs once; its `extra_data` has no `prescreen` key and has `preflight == {"status": "red", "red_step": "stub"}`.
- **AC2** Fresh green receipt: one event, `escalate`, `rung None`, `gate "integrity"`, `classifier_status "off"`; model once.
- **AC3 (no evidence, no event)** Receipt stale (a stale receipt that was red included), missing, `receipt_rung` raising, ambient cwd, `preflight_rung.mode: "off"`, `preflight_rung` config-error: no `prescreen_verdict`, `check_ladder.prescreen` not called, model once. A forcing run in the same test shows the spy is live (a red receipt does call `prescreen`).
- **AC4 (kill switch)** `{"script_rung": "off"}`: no event. `"shadow"`, `None`, and a non-dict `prescreen` (`"x"`, `[]`, `None`): behave as the default. `"bogus"`, `7`: exactly one config-error event, `prescreen` not called, model once; the same with `preflight_rung.mode: "off"` (still one event).
- **AC5 (classifier never used)** `prescreen.classifier_cmd` set to a real script that records a marker file when run, red receipt: the marker is absent, `classifier_status "off"`, `extra_data` has no `prescreen` key, with `prescreen.mode: "enforce"` in the config the event `mode` is still `"shadow"` and the model runs once.
- **AC6 (degrade)** `check_ladder.prescreen` patched to raise: one config-error event, model once, the step result is the one the model call produced (passed through). `script_findings` patched to raise: same.
- **AC7 (both billing modes)** The same red-receipt run under an API-key environment (`ANTHROPIC_API_KEY` set, no OAuth token), a subscription environment (`CLAUDE_CODE_OAUTH_TOKEN` set, no API key) and one with neither: identical events and identical model-call kwargs.
- **AC8 (order)** `preflight_receipt`, then `prescreen_verdict`, then the model call.
- **AC9 (add-only)** The model-call kwargs (prompt, model, timeout, tools, `hard_gate`, `gate_label`, `stable_prefix`, `injections`, `extra_data`) of a red-receipt run equal those of the same run with `{"script_rung": "off"}`; the model is called once in both.
- **AC10 (once per step call)** The first model reply has no standalone VERDICT, so the existing completeness re-roll calls the model a second time: still exactly one `prescreen_verdict`.
- **AC11** Siblings pass unchanged (the files in `sibling_tests`); the s7 test and the pre-GREEN event key set are untouched. No existing test is amended.

## Files

In scope: `engine_py/bytedigger_engine/workflows/phase_5_integrity.py` (import, `_rung_rec`, the new block), new `engine_py/tests/test_bd218_s8_integrity_prescreen.py`, `CHANGELOG.md`, this spec and its gate files.
Not in scope: every other file, in particular `check_ladder.py`, `preflight.py`, `conformance/harness.py`, `phase_5_implement.py`, `phase_6_fix_integrity.py`, `error_codes.py` (no new error code), `flags_catalog.py` (s7 registered no catalog entry for the same flag; the owner and expiry above are the lint-visible record, as for s5-s7).
Sibling-test audit (§1a) done before this spec: `grep -l '_invoke_integrity_llm\|check_ladder\|prescreen\|preflight_receipt' engine_py/tests` gives the sibling list above; none asserts the absence of a `prescreen_verdict` from the integrity gate (the s1 AC7 amendment concerned the pre-GREEN gate only).

## Provenance (Guy 2026-10-03)

introduced: the integrity gate was wired to the receipt in s5 (#235) and never to the ladder; nothing is removed or simplified. The model stage of the integrity gate is kept: this step only adds the shadow measuring point, the evidence for a later keep / enforce / remove decision.

## LLM stages in this step

None added. The one LLM stage touched, the integrity gate, is kept as is.
