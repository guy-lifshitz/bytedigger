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

## Rollout (one PR each, usual bd process)

1. Pre-GREEN gate: replace the shadow-only block with a ladder that runs `preflight.verify_receipt` first (script, $0), then the optional classifier, then the gate. Highest cost: the gate is the most expensive per-call Opus step. Confirm against the r3 stage breakdown in doc-PR #2208 before freezing the order.
2. Spec/plan-review gate: same ladder, using `spec_cite`/`tier_gate` findings as the script rung.
3. Review and satisfaction gates: script rung from `baseline_delta_gate`/`sibling_coupling`; classifier rung optional.
4. CI-red rerun-once: rerun the same head once; green means flake.
5. Integrity / fix-integrity gates.

Rules for every step: classifier optional (no key or unreachable means the rung is skipped, never an error), provider-agnostic (command in config, works on subscription and API), only the gate approves, shadow first with a measured recall before enforce.
