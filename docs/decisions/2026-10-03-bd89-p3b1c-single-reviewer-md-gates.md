# bd#89 P3b1c: bring the orchestrator-flow md, the gate config parsers and `bytedigger.json` to the single-reviewer reality

**Status: FROZEN r1** · **Tier:** 2 (md + shell + TS + json; no engine prod `.py`) · **Class:** PROCESS (doc/config drift removal; the engine has run one composite reviewer since #139/#198/#209) ·
**Chokepoint:** the per-tier reviewer counts `simple_reviewers` / `feature_reviewers` / `complex_reviewers`. They are declared in `bytedigger.json`, parsed by both gate backends (`scripts/build-gate.sh:38-82`, `scripts/ts/build-phase-gate.ts:121-192`) and consumed by nothing (grep over the tree finds no reader). The md flow restates the same 3/6/7 numbers in five files. Removing the keys and rewriting the md removes the drift in one pass.
**Side of the seam (decision 2026-07-26 §7.1):** orchestrator-flow md, gate scripts, plugin config and plugin docs. No engine prod `.py` (that is p3b2 / p3c / p3d).
**Base:** `origin/main` `1e391ed`. **Source:** bd#89 row 2, plan comment 5955412867. Template: `2026-10-02-bd89-p3b1b-ii-aggregation-helper.md`.

## §0 Scope size

- 4 md files rewritten: `phases/phase-6-review.md`, `commands/build.md` (6.1 only), `phases/phase-0-classify.md` (one table row), `templates/dynamic-context.md` (the roster, which `commands/build.md` 6.1 points at).
- 1 json: `bytedigger.json`. 2 gate sources: `scripts/build-gate.sh`, `scripts/ts/build-phase-gate.ts`.
- 3 plugin docs touched: `docs/plugin.md` (config example + key list), `README.md:122` (one phrase). `docs/configuration.md:30-32` is p3b2's file: left as a follow-up (§4).
- 1 RED file: `tests/test_bd89_p3b1c_single_reviewer_md_gates.py` (root `tests/`, python, reads files; no engine import) plus one TS test block in `scripts/ts/__tests__/post-review-gate.test.ts` and one bats case in `tests/build-gate.bats`.
- 1 engine sibling test migrated: `engine_py/tests/test_bd89_p3b1_single_reviewer_only.py::test_ac12_guard_reviewer_counts_untouched` (§5).
- Net: about -120 md/gate lines, +40 new test lines.

## §1 Problem (measured on `1e391ed`)

- `phases/phase-6-review.md` still has: the 6-row "Review Agent Policy by Complexity" table (`:76-85`, 3/4/6/7 agents), per-agent `reviews/{agent-name}.md` persistence with a resume rule keyed on `phase_6_reviewers_expected` (`:113-166`), "Step 1: Determine Reviewer Count" listing 7 named agents, "Launch ALL agents for your tier in parallel", the launched/expected count enforcement block with `phase_6_reviewers_launched` / `phase_6_reviewers_expected: <3|4|6|7>` (`:172-210`), the "2 Haiku Task agents" fallback, and a LAUNCH RULE that names a "reviewer panel" (`:19`).
- The engine does something else: one composite reviewer writes `reviews/role-composite.md` (`_COMPOSITE_ROLE_FILE`, bd89 p3b1b-ii); `_select_reviewers` returns exactly `composite`; there is no count to check.
- `commands/build.md:105-107` (6.1 "EXACT agents mandatory ... If launched != expected -> STOP"), `phases/phase-0-classify.md:289` (`3x reviewers | 6x reviewers | 6x reviewers`) and `templates/dynamic-context.md:14,22-27` (same row, the 3/6 roster, "Launch all parallel", `<3|4|6|7>`) repeat it. `phase-0-classify.md:89` says the tier changes "reviewer count".
- Issue #128 notes COMPLEX+HIGH = 7 reviewers in one place and 9 in another. After this slice there is one reviewer everywhere, so the count part of #128 is gone (undefined `$CONSTITUTION_BLOCK` / `$QUALITY_GATE_BLOCK` are not touched).
- `bytedigger.json:13-15` declares 3/6/6; both gate parsers read and default to them; `docs/plugin.md:84-86,119` documents them as "declared expectation only; not live knobs". `engine_py/tests/test_bd89_p3b1_single_reviewer_only.py:716-721` pins them as a GUARD ("untouched until the md slice").
- Role files in the gates: neither gate parses `role-*.md` or counts reviewer files (grep of `scripts/` finds no `role-` token; the semantic-skip scan globs `*review*.md`). So "gates degrade on old-format artifacts" is already true; this spec pins it (AC9) instead of changing behaviour.
- Decorrelated verifier: grep of every md/gate/json in this slice's scope finds **zero** mentions (the only non-engine hit is `docs/configuration.md`, p3b2's file). AC10 pins the absence so a later revert is caught.
- Baseline on `1e391ed`: `bun test scripts/ts/__tests__` and `bats tests/build-gate.bats` recorded by the orchestrator before RED (§6); engine siblings in §5 recorded the same way.

## §2 Design (pure deletion + rewording; no new behaviour)

- **op1, phase-6 md.** In `phases/phase-6-review.md`:
  - Replace the "Review Agent Policy by Complexity" table with a table whose reviewer column is the same for every row: `1: composite reviewer (correctness, silent failures, test adequacy, types, simplification, comments; security pass on HIGH)`; the satisfaction column keeps its current values (3 Opus evaluators for COMPLEX stays, out of scope).
  - Rewrite "Scratchpad Persistence": the reviewer writes `{scratchpad_dir}/reviews/role-composite.md`; keep the VERDICT format and the confidence >=80 rule; resume rule becomes "if `reviews/role-composite.md` exists, do not re-run the reviewer". Old-format scratchpads that hold several `reviews/*.md` are ignored, not an error (one sentence).
  - Replace "Step 1: Determine Reviewer Count" with "Step 1: Run the reviewer": one agent, no tier count, no parallel launch, no launched/expected enforcement, no 7-agent list, no Haiku fallback. Drop `phase_6_reviewers_launched`, `phase_6_reviewers_expected`; keep `security_review_enabled`.
  - Fix wording that assumes many reviewers: LAUNCH RULE (`:19`: drop "reviewer panel"; keep the rule for fix agents and evaluators), "Collect ALL issues from ALL review agents" -> "from the reviewer", "Re-run affected review agents" -> "Re-run the reviewer", "REMAINING FINDINGS (from review agents)", the per-agent purpose statements and Runtime/Static reviewer split (`:28-64`) -> one reviewer section. "After ALL reviewers complete" -> "After the reviewer completes".
  - **Unchanged:** Step 3 satisfaction scoring (incl. the 3 parallel Opus evaluators for COMPLEX), Step 4, Boy Scout rules, the post-review gate section.
- **op2, build.md 6.1.** Replace the 6.1 block with: `6.1 Reviewer (one composite reviewer, every tier):` -> see `phases/phase-6-review.md`; drop "EXACT agents mandatory" and "If launched != expected -> STOP". In 6.2 "Re-run reviewers" -> "Re-run the reviewer". Other lines of build.md unchanged.
- **op3, classify md + dynamic-context.** `phases/phase-0-classify.md`: the `6 Review` row reads `1x composite reviewer` in all three tiers; `:89` drops "reviewer count" from the knob list. `templates/dynamic-context.md`: same row; "Review Agent Roster" section becomes a one-line "Review Reviewer" section (one composite reviewer, no launch count). No `<3|4|6|7>` left.
- **op4, config.** Delete `simple_reviewers`, `feature_reviewers`, `complex_reviewers` from `bytedigger.json`. Keep `reviewers.mode` (read by `loadConfig`, tested by P6-F10-*).
- **op5, gate parsers (degrade, not fail).**
  - `scripts/ts/build-phase-gate.ts`: remove the three fields from `BytediggerConfig`, the defaults and the `loadConfig` assignments, and `parseReviewerCount` (now unused). `loadConfig` must still accept a config file that carries the three legacy keys (extra keys are ignored by construction).
  - `scripts/build-gate.sh`: remove `SIMPLE_/FEATURE_/COMPLEX_REVIEWERS` defaults, the three python `print`s and the three `grep|cut` assignments. A config that still carries the legacy keys must not change gate behaviour or exit code.
- **op6, docs.** `docs/plugin.md`: delete the three keys from the JSON example (`:84-86`) and the key-list bullet (`:119`); add one sentence under `reviewers.mode`: "Phase 6 runs one composite reviewer for every tier; the former `simple_reviewers` / `feature_reviewers` / `complex_reviewers` keys were removed and are ignored if present in an existing config." `README.md:122`: drop "reviewer counts" from the list. `reviewers.mode` text unchanged.
- **op7, CHANGELOG.** `[Unreleased]` / Removed bullet: the three config keys and their parsers; Changed bullet: phase-6 / build / classify / dynamic-context md now describe the single composite reviewer (partially addresses #128).

## §3 Acceptance criteria. RED file: `tests/test_bd89_p3b1c_single_reviewer_md_gates.py` (+ the TS and bats cases in §3b)

Reads the real repo files; no mocks. Gate behaviour ACs run the real scripts on `tmp_path` fixtures.

- **AC1** `phases/phase-6-review.md` contains none of: `phase_6_reviewers_launched`, `phase_6_reviewers_expected`, `<3|4|6|7>`, `Determine Reviewer Count`, `Launch ALL agents`, `launch security-reviewer`, `2 Haiku`, `reviews/code-reviewer.md`, `reviews/{agent-name}.md`, `pr-test-analyzer`, `type-design-analyzer`, `comment-analyzer`, `code-simplifier`, `silent-failure-hunter`.
- **AC2** `phases/phase-6-review.md` names `reviews/role-composite.md` at least once and still contains `VERDICT: PASS` and `phase_6_findings_skipped` (GUARD half: the second and third strings are green before GREEN; the first is red).
- **AC3** In `phases/phase-6-review.md` the review-policy table has no row whose reviewer column matches `^\|.*\|\s*[2-9]:` (no "N: ..." agent counts) and no line matches `[3467] agents` or `[3467]x reviewers`.
- **AC4** `commands/build.md` has no `EXACT agents`, no `launched ≠ expected` / `launched != expected`, and its 6.1 block references `phases/phase-6-review.md`.
- **AC5** `phases/phase-0-classify.md` and `templates/dynamic-context.md` contain no `3x reviewers`, `6x reviewers`, `<3|4|6|7>` nor `Review Agent Roster`; both contain `1x composite reviewer`; `phase-0-classify.md` does not say `reviewer count`.
- **AC6** `bytedigger.json` parses, has no key matching `*_reviewers`, and `cfg["reviewers"] == {"mode": "auto"}` (GUARD half green before GREEN).
- **AC7** Neither `scripts/build-gate.sh` nor `scripts/ts/build-phase-gate.ts` contains `simple_reviewers`, `SIMPLE_REVIEWERS`, `feature_reviewers`, `complex_reviewers` or `parseReviewerCount`; `scripts/ts/build-phase-gate.ts` still exports `parseReviewerMode`.
- **AC8** `docs/plugin.md` and `README.md` contain none of the three legacy key names outside the one "were removed and are ignored" sentence in `docs/plugin.md`; `docs/plugin.md` contains `composite reviewer`.
- **AC9** (degrade, GUARD) With a scratchpad/`cwd` holding several old-format `reviews/role-<slug>.md` files plus `role-composite.md`, and a config file that still carries the three legacy keys (3/6/6), running `scripts/build-gate.sh` for phase 6 exits with the same code as with a config without them, and `loadConfig()` (TS) returns a config object without throwing. Implemented as one bats case (gate exit code equality) and one TS test. Green before and after GREEN (the legacy keys are ignored both ways).
- **AC10** (GUARD) Across every `*.md`, `*.sh`, `*.ts` (not in `__tests__`), `*.json` outside `docs/decisions/`, `engine_py/`, `CHANGELOG.md`, `docs/configuration.md` and node_modules, the strings `decorr` and `decorrelated` (case-insensitive) do not occur.
- **AC11** (GUARD) The satisfaction step is untouched: `phases/phase-6-review.md` still contains `3-Agent Majority Vote` and `Launch 3 Opus agents in parallel`; `bytedigger.json` still has `satisfaction_thresholds` with 80/85/90.
- **AC12** (GUARD) `bun test scripts/ts/__tests__/post-review-gate.test.ts` P6-F10-01..07 (reviewers.mode) stay green.

### §3b Added tests in existing files

- `scripts/ts/__tests__/post-review-gate.test.ts`: one test `P6-F10-08` "config carrying legacy *_reviewers keys loads, mode default kept, no *_reviewers fields on the result" (covers AC7 behaviour + AC9 TS half).
- `tests/build-gate.bats`: one `@test` "legacy *_reviewers keys in bytedigger.json are ignored (gate exit code unchanged)" (AC9 bash half).

### §1w op <-> AC map

op1 -> AC1, AC2, AC3, AC11 · op2 -> AC4 · op3 -> AC5 · op4 -> AC6 · op5 -> AC7, AC9, AC12 · op6 -> AC8 · op7 -> none (doc) · (AC10 guards the decorrelated-verifier absence across all of the above).

### §3 expected-red summary

Before GREEN, AC1, AC2 (first clause), AC3, AC4, AC5, AC6 (first clause), AC7, AC8 and TS P6-F10-08's "no `*_reviewers` fields" assertion are red; AC2 (guard clauses), AC6 (mode clause), AC9, AC10, AC11, AC12 are green. A GUARD that comes out red before GREEN is a RED bug.

## §4 Out of scope (§1v: files and behaviours NOT in this PR)

- Engine prod `.py`, `error_codes`, `ERROR_CODES.md`, `org_config`, model roles: p3b2 / p3c / p3d. This slice has no decorrelated-verifier text to remove (§1); engine removal is p3b2's.
- `docs/configuration.md:30-32` still lists the three removed keys ("Declared expectation only"). That file is p3b2's. Follow-up: delete those three rows after both PRs merge. `docs/configuration.md` is excluded from AC10 for the same reason.
- The multi-evaluator SATISFACTION step (3 Opus evaluators for COMPLEX): untouched (AC11); follow-up.
- `scripts/ts/build-phase-gate.ts` / `build-gate.sh` semantic-skip scan matches `*review*.md`; `reviews/role-composite.md` does not match that glob, so the composite reviewer's own file is not scanned. Pre-existing, behaviour change not in a deletion slice: PR-body follow-up.
- Issue #128's undefined `$CONSTITUTION_BLOCK` / `$QUALITY_GATE_BLOCK`: not touched (PR says "partially addresses #128").
- `agents/synthesizer.md`, `learning-store.sh`, `learnings-raw.md`: p3c.
- `docs/article.md` / `docs/security.md` mentions of "reviewers" are prose about the pipeline in general, not counts: unchanged.

## §5 Scope list (§1a sibling-test audit). RED edits these; GREEN treats tests as read-only (§1s)

Candidate set: every test that mentions the three legacy keys, `phase_6_reviewers_*`, the md files above, or reads those md by text. Measured by grep on `1e391ed`.

1. `engine_py/tests/test_bd89_p3b1_single_reviewer_only.py::test_ac12_guard_reviewer_counts_untouched` (`:714-721`). Rewrite: assert the three keys are absent from `bytedigger.json` and from both gate sources; keep `cfg["reviewers"] == {"mode": "auto"}`. This is the only test that pins the old values.
2. `engine_py/tests/test_bd89_p3b1_single_reviewer_only.py::test_ac12_no_other_md_names_review_fanout` (`:700`): audit only; must stay green (the rewritten md must not contain `review_fanout`).
3. Any other test reading `phases/phase-6-review.md`, `commands/build.md`, `phases/phase-0-classify.md` or `templates/dynamic-context.md` as text (`tests/test_bd190_build_md_hook_truth.py`, `tests/test_worker_deliverables.py`, `engine_py/tests/*` that read those md): RED author greps for each file name and each removed string from AC1/AC4/AC5, lists the hits in the RED commit message, and adjusts only pins on removed text (never loosen a pin on surviving text).
4. `scripts/ts/__tests__/*.test.ts`, `tests/*.bats`: audit for the legacy keys and `reviewers`; `post-review-gate.test.ts:499-505` (P6-F10-06 old-style nested object) must stay green.
5. Engine tests that parse `phase_6_reviewers_*` state fields or the `Review Agent Roster`: grep; none expected.

Verify scope (§1r):
- the RED file, the added TS/bats cases, and the files in §5.1-5.5;
- `bun test scripts/ts/__tests__` and `bats tests/*.bats` (the gate suites, whole);
- `pytest tests/` (root);
- engine: `engine_py/tests/test_bd89_p3b1_single_reviewer_only.py` and every `engine_py/tests/*phase_6*` file that reads md (baseline recorded before RED, post-GREEN 0 failed);
- `python3 cyrillic-prose-lint.py` and an English-only check over the diff.

The full engine suite is CI only.

## §6 Resolved (orchestrator, 2026-10-03, AUTO-DECISION)

- **Delete the three count keys rather than default them to 1.** Why: nothing reads them; a default of 1 would still be a dead knob and a third place to keep in sync. Old configs that carry them keep working because JSON/python ignore unknown keys (AC9).
- **Keep `reviewers.mode`.** Why: it is read by `loadConfig` and has seven tests; whether it is live for a single composite reviewer is a separate question (follow-up).
- **`templates/dynamic-context.md` is in scope though not named in the lead's list.** Why: `commands/build.md` 6.1 points at it for the roster, so rewriting 6.1 alone would leave the 3/6 roster as the live source.
- **No gate role-file parser exists, so op5 is config-only.** The lead's "role-file / reviewer-count parsers" reduces to the count parsers; AC9 pins degrade behaviour for old role files.

### GAP list (not ported)

- Semantic-skip scan does not cover `role-composite.md` (see §4); `reviewers.mode` liveness; `docs/configuration.md:30-32`.
