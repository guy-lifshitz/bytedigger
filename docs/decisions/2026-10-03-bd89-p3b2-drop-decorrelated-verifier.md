# bd#89 P3b2: drop the decorrelated verifier from the engine

**Status: FROZEN r1** · **Tier:** 3 (engine prod `.py`, Option D) · **Class:** PROCESS (dead-layer removal; last piece of bd#89 row 2: one reviewer plus the existing citation and quote verification is the review) ·
**Chokepoint:** the phase-6 step list in `phase_6_review_workflow()` (`engine_py/bytedigger_engine/workflows/phase_6_review.py:5955-5957`). The three decorr steps are the only prod callers of `_build_decorr_prompt`, `_invoke_decorr_llm` and `_write_decorr_artifact`, the only reader of `org_config["decorrelated_verify_enforce"]` (`:5606`), the only emitter of the `decorr_verify_*` events, the only raiser of `E_DECORR_INVOKE_FAILED` (`:5550`) and `E_DECORR_VERIFY_SUSPECT` (`:5620`), and the only caller of `get_claude_decorrelated_verifier` (`:5541`, `:5602`).
**Side of the seam (decision 2026-07-26 §7.1):** engine, `ERROR_CODES.md` (both copies), `docs/configuration.md`, CHANGELOG. No orchestrator-flow md, gate script, `bytedigger.json` or TS: every md/gate/json mention of the decorrelated verifier is owned by p3b1c (measured on HEAD: there is none outside the files above).
**Base:** `origin/main` `1e391ed`. **Source:** bd#89 option A, row 2. Template: `2026-10-02-bd89-p3b1b-ii-aggregation-helper.md`; ignored-key precedent: `review_fanout_ignored` (P3b1, `phase_6_review.py:746-748`).

## §0 Scope size

- 3 prod files: `workflows/phase_6_review.py` (about -170 lines), `lib/model_config.py` (-4 lines), `error_codes.py` (-2 lines).
- 4 docs: `engine_py/ERROR_CODES.md` and `engine_py/bytedigger_engine/ERROR_CODES.md` (byte-identical pair), `docs/configuration.md` (one sentence), CHANGELOG.
- 1 RED file: `engine_py/tests/test_bd89_p3b2_drop_decorrelated_verifier.py`.
- 15 sibling test files audited (§5). One deletion: `test_gh379_decorr_verify.py` (86 decorr mentions).
- Phase 6 goes from 20 steps to 17.

## §1 Problem (measured on `1e391ed`)

- `len(phase_6_review_workflow().steps) == 20`. Steps 14-16 are `build_decorr_prompt`, `invoke_decorr_llm` (`resume_sentinel=True`) and `write_decorr_artifact`, between `verify_fix_typecheck` and `build_satisfaction_prompt`.
- The step is a second, adversarial Opus/Fable pass over the fix (FEATURE and COMPLEX only; SIMPLE skips). It has run in advisory mode since GH379. `enforce` is off unless an org sets `decorrelated_verify_enforce`. The flip-to-enforce backstop (`flip-by:2026-08-01`, `:5605`) was never exercised. In advisory mode the step costs one LLM call per fix cycle and its verdict changes nothing except an event.
- The model role `decorrelated_verifier` (`lib/model_config.py:37`, `:170-171`) exists only for it. `docs/configuration.md:272` lists it as a reader of the role template. The three decorr prompt builders also appear in the `role_template_path` docstring (`phase_6_review.py:47`) and in the module docstring step list (`:86-89`).
- `org_config.decorrelated_verify_enforce` can be set in a pre-upgrade org config. After the removal nothing reads it. It must not raise.
- No timeout key exists for the decorr step (measured: no `decorr` string in any timeout table).
- Decorr mentions in tests (measured): 15 files (list in §5), 86 mentions in `test_gh379_decorr_verify.py` alone.
- Baseline of the §5 files plus `*phase_6*`, `*error_code*`, `*class_i*`, `*conformance*`: recorded by the orchestrator in the RED commit message.

## §2 Design: pure deletion plus one ignored-key event

- **op1, steps.** Delete the three `StepContract` entries (`:5955-5957`) and the functions `_build_decorr_prompt`, `_invoke_decorr_llm`, `_write_decorr_artifact` with `DECORR_DOC_RELPATH` and `_DECORR_VERDICT_MARKERS` (`:5458-5624`). Remove the then-unused imports `get_claude_decorrelated_verifier` (`:140`) and `last_line_anchored_marker` (`:141`) if nothing else in the file uses them (grep before removing). `VERDICT_SUSPECT` stays (review path). Rewrite the docstrings at `:47` (three prompt builders, no `decorr`) and `:86-89` (step list matches the real 17 names).
- **op2, ignored key, visible.** In `_build_review_prompt`, next to the `review_fanout` check (`:746-748`): `_raw_enforce = (ctx.org_config or {}).get("decorrelated_verify_enforce") if ctx else None`. When `bool(_raw_enforce)` is true (same truthiness the removed reader used) emit one `decorrelated_verify_enforce_ignored` event with `{"value": str(_raw_enforce)}` and carry on. `None`, absent, `False`, `0` and `""` emit nothing. No error, no change to the prompt. `_build_review_prompt` runs once per phase-6 run, so the event is once per run.
- **op3, model role.** Delete `"decorrelated_verifier"` from the role table (`model_config.py:37`) and `get_claude_decorrelated_verifier` (`:170-171`). Remove the key from `tests/fixtures/models_pinned.json`. A `models.json` that still lists `decorrelated_verifier` must keep loading: the loader must not reject unknown role keys. GREEN verifies this by reading the loader. If it rejects them, the spec stops there (report to orchestrator).
- **op4, error codes.** Delete `E_DECORR_INVOKE_FAILED` and `E_DECORR_VERIFY_SUSPECT` from `error_codes.py` and the whole `## E_DECORR` section from both `ERROR_CODES.md` copies, keeping them byte-identical.
- **op5, docs.** `docs/configuration.md:271-272`: drop "and decorrelated verifier" (the list becomes "...satisfaction, and phase 7 synthesizer"). Add a short `decorrelated_verify_enforce` note only if configuration.md already documents `review_fanout` in a way that makes the pair consistent; otherwise CHANGELOG alone carries it. No README change (no mention on HEAD).
- **op6, CHANGELOG.** `[Unreleased]` / Removed bullet: the three steps (phase 6 goes from 20 steps to 17), `decorrelated_verify_enforce` ignored with the new `decorrelated_verify_enforce_ignored` event, the `decorrelated_verifier` model role, the events `decorr_verify_verdict`, `decorr_verify_skipped`, `decorr_verify_invoke_failed`, the artifact `reviews/build-decorr-verify.md`, and the two error codes.

## §3 Acceptance criteria. RED file: `engine_py/tests/test_bd89_p3b2_drop_decorrelated_verifier.py`

Real `EventLog` on `tmp_path`, real scratchpad dirs, no mocks of production functions (§1l). Only the LLM boundary may be stubbed.

- **AC1** `phase_6_review_workflow().steps` has 17 entries; the names equal the current 20 minus `build_decorr_prompt`, `invoke_decorr_llm`, `write_decorr_artifact`, in order; `verify_fix_typecheck` is immediately followed by `build_satisfaction_prompt`.
- **AC2** `phase_6_review` has none of the attributes `_build_decorr_prompt`, `_invoke_decorr_llm`, `_write_decorr_artifact`, `DECORR_DOC_RELPATH`, `_DECORR_VERDICT_MARKERS`.
- **AC3** (end to end, §1l) With `org_config = {"decorrelated_verify_enforce": True, ...}` and a real event-log sink, run `_build_review_prompt` on a valid scratchpad. The result is `ok` with no error code. The sink holds exactly one `decorrelated_verify_enforce_ignored` event whose `data["value"] == "True"`.
- **AC4** For `org_config` values unset, `None`, `False`, `0` and `""` the sink holds no `decorrelated_verify_enforce_ignored` event and the result is `ok`. For `"yes"` exactly one event with `data["value"] == "yes"`.
- **AC5** `model_config` has no attribute `get_claude_decorrelated_verifier`, and its role table has no `decorrelated_verifier` key. A `models.json` that still carries `{"claude": {"decorrelated_verifier": "fable"}}` loads without raising, and `get_claude_critical()` and `get_claude_primary()` still resolve. This uses the existing `model_config_fixture` helper from `test_gh386_model_pin_removal.py`.
- **AC6** `E_DECORR_INVOKE_FAILED` and `E_DECORR_VERIFY_SUSPECT` appear neither in `error_codes.ERROR_CODES` nor in either `ERROR_CODES.md`, and `python3 -m bytedigger_engine.error_codes --check` exits 0.
- **AC7** Source text: `phase_6_review.py`, `model_config.py` and `error_codes.py` contain no `decorr` (case-insensitive) except the one event name `decorrelated_verify_enforce_ignored` and the config key `decorrelated_verify_enforce` in `phase_6_review.py`. `docs/configuration.md` has no `decorrelated verifier`.
- **AC8** `tests/fixtures/models_pinned.json` has no `decorrelated_verifier` key. The pin-lock test that reads it still passes (checked by the §5 sibling rule, not by this file).
- **AC9** (GUARD) The `role_template_path` docstring still lists review, fix and satisfaction as prompt builders. `_build_review_prompt`, `_invoke_review_llm`, `verify_findings`, `verify_findings_semantic`, `_build_satisfaction_prompt` and `_write_review_artifact` still exist.
- **AC10** (GUARD) A real aggregation run (as in AC3 of the P3b1b-ii RED: `reviews/role-composite.md` with one finding quoting a real file) through the write step still writes `# Composite Review` with the finding. The citation and quote verification path is untouched.
- **AC11** (GUARD) `python3 -m bytedigger_engine.conformance.class_i_lint` exits 0.

### §1w op <-> AC map

op1 -> AC1, AC2, AC7, AC9, AC10 · op2 -> AC3, AC4, AC7 · op3 -> AC5, AC7, AC8 · op4 -> AC6 · op5 -> AC7 · op6 -> none (doc). AC11 guards the class-I inventory against stale keys.

### §3 expected-red summary

Before GREEN, AC1-AC8 are red and AC9-AC11 are green. A GUARD that comes out red before GREEN is a RED bug. The sibling reds expected from §5 edits are listed in the RED commit message, by file and test.

## §4 Out of scope (§1v: files and behaviours NOT in this PR)

- `phases/*.md`, `commands/*.md`, `scripts/*`, `bytedigger.json`: p3b1c.
- `phase_7_synthesize`: p3c. Flags catalog: p3d.
- `_aggregate_and_write_review_artifact`, `_verify_finding_quote`, citation verification, `verify_findings*`: unchanged.
- The multi-evaluator satisfaction step stays (the #89 follow-up).
- `lib/verdict_parse.py` itself stays; only the unused import in `phase_6_review.py` goes.
- Historical `docs/decisions/*` that mention decorr are history and are not edited.
- No new behaviour beyond the op2 event.

## §5 Scope list (§1a sibling-test audit). RED edits these; GREEN treats tests as read-only (§1s)

Candidate set: every test file that mentions `decorr` (15 files, measured) plus every file that pins the phase-6 step count or a step index.

1. **Delete** `test_gh379_decorr_verify.py` (all tests pin the removed steps, role, events, error codes or enforce mode). Its 20-step pin is covered by AC1. The RED author confirms per test that none guards a surviving behaviour (e.g. a fresh-session or role-template check that lives on in another file) before deleting, and notes the confirmation in the RED commit message.
2. **Step-count and step-name pins.** Change 20 to 17 and drop the three decorr names from pinned lists: `test_phase_6_mass_unverified_5F9817F6.py:334`, `test_bd141_p6_review_gate.py:35-39` (`PINNED_STEPS`), `test_bd89_p3b1_single_reviewer_only.py:660` (`_PHASE6_STEPS`), `test_bd89_p3b1b_ii_aggregation_helper.py:48`. Audit `test_291189a0_phase6_step_factory.py`, `test_bd141_p6_start_gate.py` and `test_phase_6_review_W2.py` for index or count pins.
3. **Adjacency pins "verify_fix_typecheck is immediately before build_decorr_prompt".** Retarget to `build_satisfaction_prompt`: `test_gh1591_fix_gate_boundary.py:1336-1350`, `test_phase_6_post_fix_pytest_gate_7A940850.py:140-143`, `test_phase_6_post_fix_typecheck_gate_GH316.py:287-290`.
4. **Model role.** `test_gh386_model_pin_removal.py:75-123`: the decorr role cases are the only users of that role in the fallback-chain tests. Retarget each case to a surviving role from the table (`critical` or the closest equivalent that has a fallback list), keeping the intent (fable available, unavailable via models.json, via env, via pinned full id, successor, empty list). `test_gh451_llm_provider_port.py:244`: drop the `decorrelated_verifier` row from the role-to-getter map. `test_bd82_role_backend_effort.py:138-158`: drop the decorr call from the "reviewer, verifier, semantic verifier" test and fix the docstring.
5. **Prompt-builder site tables.** `test_bd119_role_template.py:809,1044,1159-1167`: delete the `p6-decorr` row and `_DECORR_ANCHOR`. `test_bd141_p4d_role_template_injections.py:24,377-401,672-678,756`: delete `_d_decorr`, the `phase_6_decorr` entry, `test_ac4b_decorr_does_not_inherit_stale_record` and the `phase_6_review:5514` site row (renumber the module docstring). `test_bd147_injected_segments.py:614-636`: drop the decorr half of the chokepoint test; keep the fix-site assertions. `test_bd82_fresh_sessions_thread_ctx.py:222-229` (`test_f6c_decorrelated_verifier_asks_for_a_fresh_session`): delete. `test_gh1626c_worker_interpreter.py:1083`: comment only, update the wording.
6. **Fixture.** `tests/fixtures/models_pinned.json`: remove the `decorrelated_verifier` key. The test that reads it is audited in rule 4.
7. **Error-code anchors.** Grep for `E_DECORR_` across tests. Any "live code" anchor swaps to `E_MISSING_SCRATCHPAD`, which is still live.

Verify scope (§1r):
- the RED file;
- every file named above plus `test_291189a0_phase6_step_factory.py`, `test_bd141_p6_start_gate.py`, `test_phase_6_review_W2.py`;
- every `tests/*phase_6*`, `*model_config*`, `*error_code*`, `*class_i*`, `*conformance*`, `*role_template*`, `*injected_segments*` file;
- `error_codes --check`, `compileall`, `class_i_lint`, the Cyrillic lint.

The full suite is CI only.

## §6 Resolved (orchestrator, 2026-10-03, AUTO-DECISION)

- **Ignored key, not an error, and only for a truthy value.** Why: a pre-upgrade config that sets it must keep building (degrade, do not crash). `False` or absent is the default and changes nothing, so it stays quiet, like `review_fanout: "single"`.
- **Event emitted from `_build_review_prompt`.** Why: it is the first step of phase 6, it already hosts the `review_fanout_ignored` check, and it runs once per run.
- **Model role removed, not kept as an unused getter.** Why: no other caller, and an unused role would keep a pin and a fallback list alive that nobody can reach. Old `models.json` files that still list it must keep loading (AC5).
- **`last_line_anchored_marker` import removed only if unused after the deletion.** GREEN greps first. The helper stays in `lib/verdict_parse.py`.

### GAP list (not ported)

- None. The `_run_satisfaction_evaluators_parallel` family remains the #89 follow-up and is out of scope.

## §8 Provenance (audit hal#2320 section 6, verdict: remove)

- **Decorrelated verifier steps, `decorrelated_verify_enforce`, `decorrelated_verifier` role, `E_DECORR_*` codes.** introduced: GH379 (Class 5 decorrelated verifier) / agreement 769EFDA3; code arrived in the engine import (`da4d41e`, `06fefc3`), re-synced in `c75c11c` (hal#1145) — what it protected against: a second adversarial pass over the fix, advisory-only; enforce was never flipped (the 2026-08-01 backstop was never exercised) and the audit found 0 real emissions, so it never fired — why it can go now: removed for never firing; the surviving review path keeps the single reviewer plus citation and quote verification. No incident is tied to it.

### §8a Closed (audit verdict: remove)

The three test removals made by the RED agent are accepted: `test_ac4b_decorr_does_not_inherit_stale_record`, `test_f6c_decorrelated_verifier_asks_for_a_fresh_session`, and the decorr half of `test_ac7_forwarded_stale_record_dispatches_ok_through_chokepoint`. `test_gh379_decorr_verify.py` is deleted by this slice.

- **Open point 1 resolved:** the dispatch-count pin 14 to 13 is the consequence of removing the `invoke_decorr_llm` site row.
- **Open point 2 resolved:** AC3/AC4 read the event as `payload["value"]` (the test sink stores `(event_type, payload)`); the spec's `data["value"]` meant the same field.

## §9 Errata r1 (gate r1 APPROVED, `2026-10-03-bd89-p3b2-gate-r1.md`; not a re-freeze)

- **F1.** AC8 is a migration post-condition: the fixture key was removed by RED under §5 rule 6, so AC8 is green before GREEN. Expected red: AC1-AC7. Green: AC8 (post-condition), AC9-AC11 (guards). op3's fixture clause is done by RED.
- **F2.** In AC9 and §4 the symbols are `_verify_findings` and `_verify_findings_semantic` (prod `:2252`, `:2274`).
- **A1.** "Once per run" in §2 and §6 means once per `_build_review_prompt` call (once per review attempt).
- **A3.** CHANGELOG notes that phase-6 step indices from 14 onward shift by 3.
- **A4.** The "0 emissions" figure is attested by audit hal#2320 and was not measured here.
