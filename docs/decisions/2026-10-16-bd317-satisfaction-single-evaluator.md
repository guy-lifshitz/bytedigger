---
red_tests: [engine_py/tests/test_bd317_satisfaction_single_evaluator.py, engine_py/tests/test_bd317_satisfaction_retargets.py]
sibling_tests: [tests/test_bd89_p3b1c_single_reviewer_md_gates.py, engine_py/tests/test_bd89_p3b1_single_reviewer_only.py, engine_py/tests/test_phase_6_satisfaction_gate_strict_and_9702b73f.py, engine_py/tests/test_bd85_retry_budgets.py, engine_py/tests/test_gh751_satisfaction_spec_anchor.py, engine_py/tests/test_gh432_ac_checklist_parser_contract.py, engine_py/tests/test_gh388_ac_checklist_satisfaction.py, engine_py/tests/test_gh925_terminal_fail_sentinel_invalidation.py, engine_py/tests/test_bd141_p4d_role_template_injections.py, engine_py/tests/test_bd82_fresh_sessions_thread_ctx.py, engine_py/tests/test_gh705_satisfaction_stable_prefix.py, engine_py/tests/test_subagent_return_discipline_satisfaction_1A07C325.py, engine_py/tests/test_bd206_class_m_sites.py, engine_py/tests/test_gh497_telemetry_hygiene.py]
paths: [engine_py/bytedigger_engine/workflows/phase_6_review.py, engine_py/bytedigger_engine/error_codes.py, engine_py/ERROR_CODES.md, engine_py/bytedigger_engine/ERROR_CODES.md, engine_py/bytedigger_engine/conformance/class_i_inventory.json, phases/phase-6-review.md, phases/phase-0-classify.md, templates/dynamic-context.md, CHANGELOG.md]
---
# bd#317 (slice of bd#89): COMPLEX satisfaction uses the single evaluator path

**Status: FROZEN r1 (gate r1 PASS, hal-gate-agent-lite)** · **Tier:** 2 (engine prod `.py` + md, gate `hal-gate-agent-lite`) · **Class:** PROCESS (dead redundancy removal: three identical calls of one model with a vote cost 3x and add no independent view; the md layer describes behaviour the code never had) ·
**Chokepoint:** `_invoke_satisfaction_llm` (`engine_py/bytedigger_engine/workflows/phase_6_review.py:3411`). The `if cfg.get("complexity") != "COMPLEX"` fork at `:3433` is the only place that routes COMPLEX to `_run_satisfaction_evaluators_parallel` (`:3209`, only caller `:3453`). The result of that fork is consumed at one point, `_write_satisfaction_doc` (`:3637-3646`), which branches on `is_multi_evaluator` to `_write_satisfaction_doc_multi` (`:3977`, only caller `:3646`). Removing the fork at the producer and the branch at the consumer leaves exactly one satisfaction path for every tier.
**Side of the seam (decision 2026-07-26 §7.1):** engine, `ERROR_CODES.md` (both copies), `phases/phase-6-review.md`, `phases/phase-0-classify.md`, `templates/dynamic-context.md`, CHANGELOG. No `bytedigger.json` change, no TS.
**Base:** `origin/main` `e027c49`. **Source:** issue #317 (owner decision 2026-10-16: three identical calls, keep one).

## §0 Step 0 (measured on `e027c49`): still reproduces, prompts are identical

`_invoke_satisfaction_llm` passes the one `prev.data["prompt"]` to `_run_satisfaction_evaluators_parallel(prompt, model, ...)` (`:3453`), which submits `n=3` calls with the same `prompt=`, `model=`, `stable_prefix=`, `injections=` (`:3230-3247`). There is no per-evaluator parameter. The A/B/C focus split in `phases/phase-6-review.md:237-241` exists only in the md. The condition "prompts of the three calls are NOT identical" does not hold, so the lot proceeds.

## §1 Problem (measured)

- COMPLEX build: 3 `invoke_llm_subprocess` calls, same prompt, same model (`satisfaction_model`), `allowed_tools=["Read"]`, then `_aggregate_satisfaction` (`:3328`) takes majority and median score. SIMPLE/FEATURE: 1 call with `allowed_tools=["Read","Write"]` (the evaluator writes its own doc; `_resolve_satisfaction_source` prefers it).
- `phases/phase-6-review.md:235-246`, `:77-78` and `templates/dynamic-context.md:15,30` describe 3 Opus evaluators with different focus, majority vote, median. The code does not.
- The multi path carries machinery only it uses: `_run_satisfaction_evaluators_parallel`, `_aggregate_satisfaction`, `_write_satisfaction_doc_multi`, `_single_eval_passed`, `_multi_findings_groups`, the `is_multi_evaluator` / `evaluator_responses` keys, the events `satisfaction_multi_evaluator` and `satisfaction_multi_evaluator_degraded`, the `evaluator_index` field on the structured/AC/drift events, the error code `E_REVIEW_DEGRADED` (its only raiser is `:4234`), and the imports `concurrent.futures` and `statistics` if nothing else in the file uses them (GREEN greps).

## §2 Design: one path, deletion only (no switch: owner decision, rollback = revert PR)

- **op1, producer.** In `_invoke_satisfaction_llm` delete the COMPLEX fork and everything after it (`:3431-3490`, the comment at `:3431-3432` included). The function ends with the single `invoke_llm_subprocess(...)` call that SIMPLE/FEATURE use today (including the stale-doc unlink and `allowed_tools=["Read","Write"]`). No code reads `cfg["complexity"]` in this function any more.
- **op2, consumer.** In `_write_satisfaction_doc` delete the multi branch (`:3637-3646`) and fix the docstring (`:3625-3628`). Delete `_write_satisfaction_doc`'s sibling `_write_satisfaction_doc_multi` and `_single_eval_passed` (`:3977-4333`).
- **op3, helpers.** Delete `_run_satisfaction_evaluators_parallel`, `_aggregate_satisfaction`, `_multi_findings_groups`, and the section banner `Multi-evaluator satisfaction helpers (021D8FAE)`. Keep `_decide_satisfaction_passed`, `_deterministic_satisfaction_override`, `_satisfaction_findings_blocks`, `_persist_satisfaction_last_findings` (docstring: drop "single-eval and multi-eval"). Remove `import concurrent.futures` and `import statistics` only if grep shows no other use.
- **op4, events and fields.** `satisfaction_verdict_drift`, `satisfaction_structured_ok/missing`, `satisfaction_ac_checklist` keep their single-path payloads (no `evaluator_index`). The two `satisfaction_multi_evaluator*` events disappear with their emitter.
- **op5, error code.** Delete `E_REVIEW_DEGRADED` from `error_codes.py:204` and from both `ERROR_CODES.md` (`:313`), byte-identical pair; `python3 -m bytedigger_engine.error_codes --check` stays 0. Why: after op2 nothing raises it; a registered code with no raiser is the dead-layer shape this slice removes.
- **op6, class-I inventory.** Delete the key `workflows/phase_6_review.py::_write_satisfaction_doc_multi::read_text#0` (`class_i_inventory.json:274`); `class_i_lint` exits 0.
- **op7, md.** `phases/phase-6-review.md`: rewrite `### COMPLEX — 3-Agent Majority Vote` (`:235-246`) as `### COMPLEX — Single Opus Evaluator (5 dimensions)`, dimensions as FEATURE, threshold `>=90%`, no vote, no A/B/C, no median; lines `:77-78` column `3x Opus voting, 5 dim, >=90%` becomes `1x Opus, 5 dim, >=90%`. `phases/phase-0-classify.md:290` `3x Opus voting (5 dim)` becomes `Opus (5 dim)`; `templates/dynamic-context.md:15` `3x Opus (5D)` becomes `Opus (5D)`; `:30` becomes `| COMPLEX | 1x Opus | same as FEATURE | >=90% |`. The `satisfaction_thresholds` sentence and `bytedigger.json` stay (80/85/90).
- **op8, CHANGELOG.** `[Unreleased]` / Changed: COMPLEX satisfaction runs one evaluator (was three identical calls with a vote); Removed: the helpers, events and `E_REVIEW_DEGRADED`.
- **Threshold.** The engine reads `org_config["satisfaction_threshold"]` (set per tier by the orchestrator from `satisfaction_thresholds`; COMPLEX is 90). Unchanged. Evaluator prompt and data unchanged (`_build_satisfaction_prompt` untouched).

## §3 Acceptance criteria. RED file: `engine_py/tests/test_bd317_satisfaction_single_evaluator.py`

Real `EventLog` on `tmp_path` and a real scratchpad. Only the LLM boundary (`invoke_llm_subprocess` as imported into `phase_6_review`) may be stubbed (§1l); the stub counts calls and returns a canned evaluator response.

- **AC1** (end to end, §1l) `org_config = {"complexity": "COMPLEX", "satisfaction_threshold": 90, ...}`: run `_invoke_satisfaction_llm` then `_write_satisfaction_doc` on a valid prev. The LLM stub is called exactly once; its kwargs have `allowed_tools == ["Read","Write"]`; the first result `.data` has no key `is_multi_evaluator` and no key `evaluator_responses`.
- **AC2** same setup, canned response `SCORE: 90` (plus the structured verdict the single parser accepts, mirroring the SIMPLE fixture of `test_phase_6_satisfaction_structured_verdict.py`): the write step returns `ok` with `data["threshold"] == 90`, `data["score"] == 90`, and `data` has no key `multi_evaluator`.
- **AC3** same setup, canned `SCORE: 89`: the write step is not `ok`; the error code is `E_SATISFACTION_BELOW_THRESHOLD` (or the recoverable gate wrapper that carries it; assert on the carried code), and `data["threshold"] == 90`.
- **AC4** (GUARD, parametrized `SIMPLE`/`FEATURE`/unset) one LLM call, `allowed_tools == ["Read","Write"]`, same as before: SIMPLE/FEATURE behaviour unchanged.
- **AC5** (registry) `phase_6_review` has none of the attributes `_run_satisfaction_evaluators_parallel`, `_aggregate_satisfaction`, `_write_satisfaction_doc_multi`, `_single_eval_passed`, `_multi_findings_groups`. `_decide_satisfaction_passed`, `_deterministic_satisfaction_override`, `_satisfaction_findings_blocks`, `_persist_satisfaction_last_findings`, `_write_satisfaction_doc`, `_invoke_satisfaction_llm` still exist.
- **AC6** (source text) `phase_6_review.py` contains none of `is_multi_evaluator`, `evaluator_responses`, `satisfaction_multi_evaluator`, `ThreadPoolExecutor`, `E_REVIEW_DEGRADED`.
- **AC7** `E_REVIEW_DEGRADED` is in neither `error_codes.ERROR_CODES` nor either `ERROR_CODES.md`; `python3 -m bytedigger_engine.error_codes --check` exits 0.
- **AC8** (md) `phases/phase-6-review.md` has none of `3-Agent Majority Vote`, `Launch 3 Opus agents`, `Evaluator A`, `Evaluator B`, `Evaluator C`, `3x Opus`, and has a heading containing `COMPLEX` and `Single Opus Evaluator`. `templates/dynamic-context.md` and `phases/phase-0-classify.md` have no `3x Opus`. `bytedigger.json` `satisfaction_thresholds` equals `{SIMPLE:80, FEATURE:85, COMPLEX:90}` (GUARD).
- **AC9** (GUARD) `python3 -m bytedigger_engine.conformance.class_i_lint` exits 0 and the inventory has no key containing `_write_satisfaction_doc_multi`.

### §1w op <-> AC map

op1 -> AC1, AC4 · op2 -> AC2, AC3, AC5, AC6 · op3 -> AC5, AC6 · op4 -> AC6 · op5 -> AC7 · op6 -> AC9 · op7 -> AC8 · op8 -> none (doc).

### §3 expected-red summary

Before GREEN: AC1, AC2 (`multi_evaluator` key), AC3 (3 calls), AC5-AC8 red; AC4 and the guard halves of AC8/AC9 green. A GUARD that is red before GREEN is a RED bug.

## §4 Out of scope (§1v)

- `_build_satisfaction_prompt`, the evaluator prompt, `stable_prefix` data (still built; the single path does not pass it, as today for SIMPLE/FEATURE; wiring it is a separate cost slice).
- `bytedigger.json`, `docs/configuration.md` threshold rows, TS.
- Historical `docs/decisions/*` that mention the multi path (history, not edited).
- Reviewer fan-out (done in bd#89 P3b1).

## §5 Scope list (§1a sibling-test audit). RED edits these; GREEN treats tests as read-only (§1s)

Candidate set (measured by symbol grep on `e027c49`: `is_multi_evaluator`, `evaluator_responses`, `_aggregate_satisfaction`, `_run_satisfaction_evaluators_parallel`, `majority_passed`, `E_REVIEW_DEGRADED`, `3-Agent Majority Vote`, `3x Opus`):

1. **Delete** `engine_py/tests/test_phase_6_satisfaction_multi_evaluator.py` (all tests pin the removed multi path) and `engine_py/tests/test_phase_6_degrade_fail_loud_F81D5EF7.py` (pins `E_REVIEW_DEGRADED` of the multi path; confirm per test that none guards a surviving behaviour).
2. **Multi-fixture tests, convert to the single path or drop the multi cases:** `test_phase_6_satisfaction_gate_strict_and_9702b73f.py` (AC7-AC9 multi), `test_bd85_retry_budgets.py:413-482` (multi evals, `E_REVIEW_DEGRADED`), `test_gh751_satisfaction_spec_anchor.py:113-252`, `test_gh432_ac_checklist_parser_contract.py:149-165`, `test_gh388_ac_checklist_satisfaction.py:122-472`, `test_gh925_terminal_fail_sentinel_invalidation.py:288-424` (the `E_REVIEW_DEGRADED` case is dropped).
3. **Dispatch and pool pins:** `test_bd141_p4d_role_template_injections.py` (`phase_6_review:3044-pool` entry at `:738`, `test_ac5_dispatch_hidden_behind_executor_submit`, the COMPLEX `:678` case: the pool site disappears, dispatch-count pin drops accordingly), `test_bd82_fresh_sessions_thread_ctx.py:394-415` (pool thread-ctx test of the removed helper: delete), `test_gh705_satisfaction_stable_prefix.py` AC6 and AC8 (the removed helper: delete; the builder ACs stay), `test_subagent_return_discipline_satisfaction_1A07C325.py` AC2 (COMPLEX now passes Read+Write: retarget to AC1's expectation).
4. **bd#206 M1:** `test_bd206_class_m_sites.py:262,347-379` COMPLEX multi-block case: the COMPLEX run now yields the single-evaluator shape (one block). Retarget the COMPLEX case to the single-evaluator assertion; keep the SIMPLE/FEATURE M1 case.
5. **Error-code guard:** `test_bd89_p3b1_single_reviewer_only.py:484` drop the `E_REVIEW_DEGRADED` line from `test_ac7_guard_kept_error_codes`, keep `E_MISSING_SCRATCHPAD`.
6. **md pin:** `tests/test_bd89_p3b1c_single_reviewer_md_gates.py:243-244` (AC11) asserts `3-Agent Majority Vote` and `Launch 3 Opus agents in parallel` are still present: flip to absent (the single-evaluator heading present) and keep the `satisfaction_thresholds` 80/85/90 assertion at `:245-246`.
7. **Conformance:** `class_i_inventory.json:274` per op6.

Verify scope (§1r): the RED file; every file named above; every `engine_py/tests/*phase_6*`, `*satisfaction*`, `*error_code*`, `*class_i*`, `*conformance*`, `*role_template*` file; `tests/test_bd89_p3b1c_single_reviewer_md_gates.py`; `error_codes --check`, `compileall`, `class_i_lint`, the Cyrillic lint. Full suite: baseline on `e027c49` before RED, delta after GREEN (orchestrator records both).

## §6 Resolved (orchestrator)

- **`E_REVIEW_DEGRADED` removed, not kept.** Why: its only raiser is the deleted multi path; a registered code nobody can raise misleads readers. The one test that guards "kept" codes is edited (§5 item 5).
- **COMPLEX takes the SIMPLE/FEATURE tool set (`Read`+`Write`) and no `stable_prefix`.** Why: the issue says COMPLEX goes through the same single path. The `Read`-only tool set and the prefix existed only for the pool.
- **No config switch.** Why: owner decision; rollback is revert.
