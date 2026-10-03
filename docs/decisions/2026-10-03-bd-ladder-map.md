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

0. (Superseded by the lot-assume audit: keep / to-script / remove per layer, with provenance. No layer is removed or simplified before it lands.) Evidence per LLM stage, from existing logs only (`reject_log.py`, `reject_stats.py`, event logs; no new runs): for each gate and review, how many of its rejects/findings a script or the test suite would have caught anyway. Output: a table that decides, per stage, keep / move to script / remove.
1. Pre-GREEN gate: run `preflight.verify_receipt` first (script, $0). ADD-ONLY for now: the receipt verdict is run and recorded as a cheap rung and the gate still always runs. The variant that skips the gate on a fresh green receipt is PAUSED until the lot-assume audit lands. The optional classifier sits between, shadow only until recall is measured.
2. Spec/plan-review: same, with structural findings only (`tier_gate`, `spec_coverage`) as the script rung. Wording checks (citation format, negation regex, prose markers) are out of scope.
3. Review and satisfaction gates, and retries: script rung from `baseline_delta_gate`/`sibling_coupling`; stages that step 0 shows redundant are dropped.
4. CI-red rerun-once: rerun the same head once; green means flake.
5. Integrity / fix-integrity gates.

## Provenance rule (Guy 2026-10-03)

Before removing or simplifying any layer, gate, check or flag, find why it was introduced (git log -S/blame, GH issue, continuity agreement, learning, incident). Each PR carries one line: `introduced: <link> -- what it guarded -- why it can go now / what it becomes (script)`. A layer that guarded against a real incident is not deleted; it is converted into a cheap check. No provenance found: say so in the line.

Step 0 outputs this line per stage. So far: nothing removed or simplified by this lot. The validation gate was ported from HAL (`never_skip_opus_validation_gate`, HAL `workflows.md` step 3); bd git history shows it only from the engine extraction (da4d41e, 2026-07-16), so incident provenance for it is still to be found in the HAL post-mortems before step 1 may skip it.

Fix per the audit first, measure after. Order is provisional until step 0 and the r3 stage breakdown (doc-PR #2208) are in.

Rules for every step: classifier optional (no key or unreachable skips the rung, no failure), provider-agnostic (subscription and API), only a gate approves, shadow before enforce.

## Status (2026-10-03, bd-s6)

- **Step 2 (spec/plan-review rung): covered, no PR.** Before the plan-review LLM (`invoke_review_llm`, `phase_45_spec.py:5008`) the workflow already runs 13 script steps, `verify_spec_completeness` .. `verify_spec_reality` (`phase_45_spec.py:4994-5006`), among them `verify_spec_coverage` (`_verify_spec_coverage`, `phase_45_spec.py:2952`, step at `:5003`, recoverable `E_SPEC_COVERAGE`). `tier_gate` is HAL-path-bound (`ENGINE_PY_MARKER`) and MICRO-only; the phase has no tier, so a `tier_gate` rung has nothing to read in bd. The "not run as one pre-step" row above is stale for the spec gate.
- **Step 4 (CI-red rerun-once): deferred, decision MGR 2026-10-03.** Enforce behaviour that masks flakes; not taken.
- **Step 5 (integrity gate): in progress as s5**, choice A: the engine writes a phase-green receipt (syntax/stub/facts) before the integrity gate and records the rung; shadow, add-only, 0 LLM, owner bd-ladder, expires 2026-10-17. Spec `2026-10-03-bd218-s5-green-receipt.md`.
