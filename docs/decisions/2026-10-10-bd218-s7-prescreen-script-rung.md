---
red_tests:
  - engine_py/tests/test_bd218_s7_script_rung.py
paths:
  - engine_py/bytedigger_engine/check_ladder.py
  - engine_py/bytedigger_engine/workflows/phase_5_implement.py
---
# bd#218 step 7 — the pre-GREEN gate's pre-screen gets a real script rung (SHADOW, add-only, no LLM)

**Status: r2** (applies gate r1: 2 MAJOR + minors, `2026-10-10-bd218-s7-prescreen-script-rung-gate-r1.md`) · **Tier:** 3 (two production files, Option D) · **Class:** SYSTEMATIC ·
**Chokepoint:** `check_ladder.script_findings` — the one function that turns a preflight rung record
(`preflight.receipt_rung`) into the findings handed to `check_ladder.prescreen`. The phase-5 call site adds no mapping of its own.
**Side of the seam:** engine. **Source:** bd#218, `2026-10-03-bd-ladder-map.md` (first slice: pre-GREEN gate); builds on
bd#141 item 3 (#144), s1 (#228), s2 (#230).
**Owner:** bd-ladder lot · **expires:** 2026-10-24 (+14 d): by then the script rung has shadow evidence and a keep/enforce/remove decision.

## §1 Problem (measured on f7e7aa7)

The ladder is three rungs, script → classifier → gate. At the pre-GREEN validation gate (`phase_5_implement._invoke_validation_llm`):

1. `check_ladder.prescreen` is called with `findings=[]` (hard-coded), so the script rung can never fire.
2. The call exists only when `prescreen.classifier_cmd` is set. With no classifier (the default, and the r3 A/B stand) there is no `prescreen_verdict` event at all.
3. The preflight rung (s1/s2) already runs earlier in the same function and knows whether the cheap deterministic steps (syntax, stub, facts) are red on the tree
   the gate will judge — but it is computed *after* the pre-screen block and never reaches it.

Result: a real run shows no script-rung verdict, although the data for one is already in hand.

## §2 Design — SHADOW, ADD-ONLY

**The validation gate runs exactly once per call, whatever the ladder says. The pre-screen is always `mode="shadow"`: a reject is recorded, never acted on. No LLM call, no network, no test run is added.**

### op1 `check_ladder.script_findings(rung) -> list[Finding]`
Pure, stdlib, never raises. `rung` is the dict `preflight.receipt_rung` returns. Only `rung["status"] == "red"` yields findings:
`[{"rule": "preflight:<red_step>", "severity": "MAJOR", "detail": "preflight step <red_step> is red on the tree the gate judges"}]`
(`red_step` missing, not a string, or blank → `unknown`). Every other input (fresh, stale, missing, error, a non-dict, `None`) → `[]`.
`stale` is deliberately not a finding: a stale receipt describes another tree (s1 reading rule: only `fresh`/`red` carry information).
Findings pass `check_ladder._validate` (rule/detail strings, severity MAJOR).

### op2 reorder + feed in `_invoke_validation_llm`
The pre-screen block moves to *after* the preflight rung block (a move plus the edits below; the producer and rung code are untouched). The rung block keeps the
record it already computes (`_pr_rec`) in a local `_rung_rec` (`None` when the rung was off, ambient-skipped, config-error or errored).
- `findings = check_ladder.script_findings(_rung_rec)` when the script rung is enabled, else `[]` (today's value).
- **When the pre-screen runs:** classifier configured (as today) **or** script rung enabled with `_rung_rec["status"] in ("fresh", "red")`. Otherwise nothing is called or emitted (today's behaviour; L16 stays byte-for-byte).
- `classifier_cmd` stays optional: unset → `prescreen` is called with `classifier_cmd=None` (classifier status `off`); set but unreachable, timing out or malformed → status `error`/`timeout`, the verdict is still produced from the script rung. Nothing raises, nothing blocks the gate (provider-agnostic: the rung reads no key and no env).
- `mode="shadow"` always. `prescreen.mode == "enforce"` is still ignored (L17); enforce is a later PR with evidence.
- Event `prescreen_verdict`: same keys as today (L17 pins the key set). With a red receipt: `outcome: "reject"`, `rung: "script"`; fresh: `outcome: "escalate"`, `rung: None`. `extra_data["prescreen"]` is added only when a classifier is configured (as today); with no classifier `extra_data` is unchanged.
- Any exception in the block → today's `config-error` event shape; the gate runs once.

### op3 kill switch
`org_config["prescreen"]["script_rung"]`: absent / `None` / `"shadow"` → on (default, shadow). `"off"` → findings `[]` and no classifier-less event (exactly today's behaviour).
Any other value (or a non-str) → treated as `"off"` for the script rung, and the call emits **exactly one** `prescreen_verdict` event, in the existing config-error shape (`classifier_status: "config-error"`, `outcome: "escalate"`), so a typo is not silent. That event replaces the verdict: `check_ladder.prescreen` is not called, no classifier is spawned even if `classifier_cmd` is set, and `extra_data` has no `prescreen` key; gate runs once. A non-dict `prescreen` value → default on.
`prescreen` raising (e.g. an invalid `timeout_s`) gives the same config-error shape (existing behaviour, also with no classifier and a red receipt).
Independent of `preflight_rung.mode` (which off also leaves `_rung_rec = None`, hence no script rung).

### Out of scope (one PR, one problem)
Enforce mode; skipping or replacing the gate; spec/plan-review, integrity, fix-integrity, review, satisfaction gates and CI-red (separate PRs of bd#218); changing the classifier contract; changing `preflight.py`, `reject_log`, `reject_stats`; reject-row fields (the s1 `detail.preflight` already carries the rung record next to the verdict).

## §3 Acceptance criteria

- **AC1** `script_findings`: red + `red_step "stub"` → one MAJOR finding, rule `preflight:stub`; red with no/blank/non-str `red_step` → rule `preflight:unknown`; fresh, stale, missing, error, `{}`, `None`, `"x"`, `7` → `[]`; result accepted by `check_ladder.prescreen` without `ValueError`; never raises.
- **AC2 (reachability, the reference case)** `_invoke_validation_llm`, default config (no `prescreen`), non-ambient real repo with a real RED receipt (`red_step == "stub"`): exactly one `prescreen_verdict` event, `outcome == "reject"`, `rung == "script"`, `mode == "shadow"`, `classifier_status == "off"`; the gate is called once; `extra_data` has no `prescreen` key.
- **AC3** Same with a `fresh` receipt: one `prescreen_verdict`, `outcome == "escalate"`, `rung is None`; gate once.
- **AC4** No classifier and receipt `stale` (including a stale receipt that was red) / `missing` / `error` (receipt_rung raising) / ambient-skip / rung `mode: off` / config-error: no `prescreen_verdict` event, `check_ladder.prescreen` not called, gate once.
- **AC5** Classifier configured (a real script returning `reject 1.0`), red receipt: event `reject`/`script`, `classifier_status == "ok"`, `extra_data["prescreen"]["findings"]` non-empty, gate once even with `mode: "enforce"` in the config.
- **AC6 (kill switch)** `{"script_rung": "off"}`: no event with no classifier; with a classifier the verdict's findings are `[]` (today). `"shadow"` and `None` behave as default (on). Unknown value (`"bogus"`, `7`): one config-error-shaped event, gate once; with a classifier configured too: still exactly one event, classifier not spawned, no `extra_data["prescreen"]`. Non-dict `prescreen` (`"x"`, `[]`, `None`) behaves as default.
- **AC7 (classifier optional, provider degrade)** Classifier command that does not exist, exits non-zero, or times out + red receipt: event produced, `classifier_status` in `error`/`timeout`, `outcome == "reject"`, `rung == "script"`, no exception, gate once.
- **AC8 (both billing modes)** The same red-receipt run under an API-key environment (`ANTHROPIC_API_KEY` set, no OAuth token) and a subscription environment (`CLAUDE_CODE_OAUTH_TOKEN` set, no API key) and an environment with neither: identical events and identical gate kwargs (the rung reads no credential).
- **AC9 (order)** `preflight_receipt` event, then `prescreen_verdict`, then the gate call.
- **AC10** `check_ladder.prescreen` raising (patched) → config-error-shaped event, gate once, result passed through unchanged.
- **AC11 (add-only)** Gate kwargs minus `extra_data["prescreen"]` / `["preflight"]` equal the run with `{"script_rung": "off"}` plus `{"mode": "off"}` rung: prompt, model, tools, timeout, `hard_gate`, `gate_label`, `stable_prefix`, `injections`.
- **AC12** Siblings pass unchanged: `test_bd141_check_ladder.py` (L16, L17 included), `test_bd164_preflight.py`, `test_bd218_s2_receipt_producer.py`, `test_bd218_s5_green_receipt.py`, the other §4 siblings of s1. **One amendment, made in RED before the freeze:** `test_bd218_s1_preflight_rung.py::test_AC7` asserted "prescreen never invoked / no `prescreen_verdict`" with no classifier; that is exactly the behaviour this step changes, so its two assertions are replaced by "classifier status `off`, no classifier process started, gate called once".

## §4 Files

In scope: `engine_py/bytedigger_engine/check_ladder.py` (new function + `__all__`), `engine_py/bytedigger_engine/workflows/phase_5_implement.py` (`_invoke_validation_llm` only),
new `engine_py/tests/test_bd218_s7_script_rung.py`, `engine_py/tests/test_bd218_s1_preflight_rung.py` (AC7 only, RED phase), `CHANGELOG.md`, this spec and its gate files.
NOT in scope: `preflight.py`, `reject_log.py`, `reject_stats.py`, every other workflow, `phases/`, `error_codes.py`.
Sibling-test audit (`--require-clean` before freeze): every file matching `grep -l '_invoke_validation_llm\|check_ladder\|prescreen' engine_py/tests`.

## §5 Provenance (Guy 2026-10-03)

introduced: bd#141 item 3 (#144) -- `findings=[]` was a placeholder: the ladder shipped before any script produced findings -- it becomes the preflight receipt's red steps (script, no LLM). Nothing else is removed or simplified.
The validation gate is kept: audit hal#2320 (the Opus stage stays until reject logs show a script catches all its findings); this step only adds the shadow measuring point.

## §6 Principles checked

1. Deterministic first: the script rung is a receipt read plus a pure mapping; the classifier is optional and below it; the gate stays the only approver.
2. Provider-agnostic: no key or env read; unreachable classifier degrades to a script-only verdict (AC7, AC8).
3. Subscription and API-token modes both exercised (AC8).
4. Shadow first, with a kill switch (`prescreen.script_rung`), owner and expiry above.

## §7 LLM stages in this step

None added. The one LLM stage touched, the pre-GREEN validation gate, is kept (§5).
