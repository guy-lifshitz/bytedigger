# Check ladder in the bd engine: map and rollout plan

State at origin/main 5d15316 + 36 (2026-10-03). No A/B runs were started for this map.

## What exists

- `check_ladder.py` (PR #144): `prescreen()` runs an optional classifier command, modes shadow/enforce. A rung may only reject or escalate, never approve.
- `preflight.py` (PR #186 / bd#164): deterministic steps syntax, tier, cite, stub, scoped, siblings, facts, prescreen. Writes a receipt; `verify_receipt()` reports fresh/stale/red/missing.

## Where the ladder is wired

| Site | Status |
|---|---|
| `phase_5_implement._invoke_validation_llm` (pre-GREEN gate) | `check_ladder.prescreen` in shadow mode only. Runs only if `org_config.prescreen.classifier_cmd` is set. Called with `findings=[]`, so the script rung contributes nothing. The verdict is ignored and the gate always runs. |
| every other LLM call site | not wired |
| `preflight.py` | not called by the engine or any phase; reachable only as a CLI |

In the r3 stand (`~/ab2208`, 2281) the event logs hold no `prescreen_verdict` event, so the ladder did not fire there (no classifier configured).

## LLM call sites and what a script already catches

| Gate / call (file) | Goes straight to LLM? | Script before it today |
|---|---|---|
| spec LLM, plan-review gate (`phase_45_spec`, hard) | yes | `spec_cite`, `spec_class`, `spec_coverage`, `tier_gate` exist as modules; not run as one pre-step |
| RED LLM (`phase_5_implement`) | n/a (producer) | n/a |
| RED-lint preflight (`red_lint_preflight`) | script | stub, 1q, suite, collect-probe; recoverable twice |
| validation gate pre-GREEN (`phase_5_implement`, hard) | yes, after shadow prescreen | `stub_passability`, `facts_pack`, `known_reds_ledger`, RED-lint only inside preflight CLI |
| integrity gate (`phase_5_integrity`, hard) | yes | none called ahead of it |
| fix-integrity gate (`phase_6_fix_integrity`, hard) | yes | none |
| review LLM (`phase_6_review`) | yes | `baseline_delta_gate`, `sibling_coupling` run elsewhere, not as a filter |
| satisfaction gate (`phase_6_review`, hard) | yes | none |
| fix / decorr / synthesizer LLM | yes | n/a |
| CI-red | no rerun-once rule found in the engine | n/a |

## Principle (Guy 2026-10-03, takes priority)

Bare C0 passes 8/8 at $14.5, so bd must not be slower or dearer in substance: C0 plus cheap checks. Every LLM stage (gate, review, retry) must show it catches what scripts and tests do not; otherwise it moves into a script or is removed. Deterministic checks first, LLM last. This plan adds no LLM call; the classifier rung is optional and off by default.

## Rollout (one PR each, usual bd process)

0. Evidence per LLM stage, from existing logs only (`reject_log.py`, `reject_stats.py`, event logs; no new runs): for each gate and review, how many of its rejects/findings a script or the test suite would have caught anyway. Output: a table that decides, per stage, keep / move to script / remove.
1. Pre-GREEN gate: run `preflight.verify_receipt` first (script, $0). Where step 0 shows the gate adds nothing over preflight, skip the gate when the receipt is fresh and green. The optional classifier sits between, shadow only until recall is measured.
2. Spec/plan-review: same, with `spec_cite`/`tier_gate` findings as the script rung.
3. Review and satisfaction gates, and retries: script rung from `baseline_delta_gate`/`sibling_coupling`; stages that step 0 shows redundant are dropped.
4. CI-red rerun-once: rerun the same head once; green means flake.
5. Integrity / fix-integrity gates.

Order is provisional until step 0 and the r3 stage breakdown (doc-PR #2208) are in.

Rules for every step: classifier optional (no key or unreachable skips the rung, no failure), provider-agnostic (subscription and API), only a gate approves, shadow before enforce.
