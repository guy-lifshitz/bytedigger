# bd#89 P3c: phase 7 writes `post-deploy-report.md` from the event log, with no LLM; drop the synthesizer agent and the `learnings-raw.md` deliverable

**Status: FROZEN r1** · **Tier:** 3 (engine prod `.py`, Option D) · **Class:** PROCESS (row 6 of bd#89: LLM step replaced by a deterministic one; the only slice with new code) ·
**Chokepoint:** `phase_7_synthesize_workflow` (`engine_py/bytedigger_engine/workflows/phase_7_synthesize.py`, last line). It is the only place that registers the three synthesizer steps (`build_synthesizer_prompt`, `invoke_synthesizer_llm`, `write_synthesizer_artifact`), and `write_synthesizer_artifact` is the only writer of `post-deploy/post-deploy-report.md` and the only raiser of the three `E_SYNTHESIZER_*` codes. Replacing the three steps with one deterministic step removes the model call, the error codes, the timeout key and the structured-verdict schema together.
**Side of the seam (decision 2026-07-26 §7.1):** engine (phase 7, error codes, timeout policy, disk_truth schema, config/inventory files), the orchestrator-flow md (`phases/phase-7-synthesize.md`, `commands/build.md` 7.1b, `agents/synthesizer.md`), the two gate scripts and `scripts/phase-deliverables.tsv`, and docs. It is a cross-seam slice by design (MGR decision, #89 comments).
**Base:** `origin/main` `1e391ed`. **Source:** bd#89 row 6 plus the MGR decision "drop the LLM-authored `reviews/learnings-raw.md` together with the LLM synth". Template: `2026-10-02-bd89-p3b1b-ii-aggregation-helper.md`.
**Ownership overlap:** p3b1c edits `scripts/build-gate.sh` and `scripts/ts/build-phase-gate.ts` for reviewer parsing, and `commands/build.md` / `phases/*.md` for the single reviewer. P3c touches only the `learnings-raw` / synthesizer hunks there (§2 op7, op8) and keeps each hunk to the minimum so that a rebase merges cleanly.

## §0 Scope size

- Prod `.py` (engine): `workflows/phase_7_synthesize.py` (799 lines, rewritten to about 300), `lib/timeout_policy.py` (1 line), `error_codes.py` (3 lines), `lib/plugins/disk_truth/schema.py` + `__init__.py` (`SynthesizerVerdict`), `config_provider` untouched.
- Config/inventory data: `lib/plugins/anti_hallucination/config.yaml` (1 entry), `security/except-pass-allowlist.txt` (1 line), `conformance/class_i_inventory.json` (synthesizer keys), `tests` fixtures none.
- Docs and md: two `ERROR_CODES.md` copies (byte-identical), `CHANGELOG.md`, `docs/configuration.md`, `docs/plugin.md`, `docs/article.md`, `phases/phase-7-synthesize.md`, `commands/build.md`, `agents/synthesizer.md` (deleted).
- Scripts: `scripts/phase-deliverables.tsv` (1 row), `scripts/build-gate.sh` and `scripts/ts/build-phase-gate.ts` (comment-only hunks, see op7).
- RED file: `engine_py/tests/test_bd89_p3c_deterministic_synthesize_report.py`.
- Sibling tests: about 30 files audited (§5); 6 deleted whole, 3 trimmed, the rest re-pointed.
- Phase 7 goes from 3 steps to 1.

## §1 Problem (measured on `1e391ed`)

- `len(phase_7_synthesize_workflow().steps) == 3`. Step 2 calls `invoke_llm_subprocess` (Haiku, `allowed_tools=["Read","Write","Glob"]`, timeout `synthesize.llm` = 600 s). The report that phase 8 reads (`post-deploy/post-deploy-report.md`: `_parse_report_summary` takes the first `Done:` line, `_derive_pr_metadata` uses the whole text as the PR body, the durable copy is taken from it at `phase_8_post_deploy.py:869-874`) therefore depends on a model.
- The LLM step can fail the phase in three ways that have nothing to do with the build: `E_SYNTHESIZER_BLOCKED` (non-recoverable), `E_SYNTHESIZER_NEEDS_CONTEXT`, `E_SYNTHESIZER_NO_MARKER` (truncated output). All three are raised only in `_write_synthesizer_artifact` (`:737-778`).
- Everything the report needs is already deterministic: `_collect_completed_phases` / `_completed_phase_digest` / `_telemetry_digest` read the event log, `_satisfaction_state` classifies the satisfaction doc, `_resolve_scratchpad` locates the files. Only the "Done / Learnings / Concerns" prose came from the model, and Learnings has one consumer, `scripts/learning-store*.sh`, which already no-ops (`learnings_extracted: 0`, exit 0) when `reviews/learnings-raw.md` is absent (`learning-store.sh:301-304`, `learning-store-sqlite.sh:305-308`).
- The model also authors `reviews/learnings-raw.md`, and two soft gates demand it: `scripts/phase-deliverables.tsv:13` (`7 scratch_file reviews/learnings-raw.md`, read by `build-gate.sh` `check_deliverables "7"` and `build-phase-gate.ts` `checkDeliverables(cwd, "7")`). A deterministic phase cannot author lessons, so the deliverable and its gate go away together (MGR decision).
- Config surface that exists only for the model call: org keys `synthesizer_model`, `synthesizer_llm_command`, `synthesizer_llm_timeout_sec` (timeout key `synthesize.llm`, `timeout_policy.py:66`), schema `SynthesizerVerdict`, role-template and injection records for the phase-7 prompt (bd#119, bd#141, bd#150), and `agents/synthesizer.md`.
- Baseline of the sibling set in §5 on `1e391ed` is recorded by the orchestrator before RED (§1r) and written into the RED commit message.

## §2 Design

**New workflow shape.** `phase_7_synthesize_workflow()` keeps `name="phase_7_synthesize"` (registered in `workflows/__init__.py`, listed in `core_manifest.json`) and has exactly one step: `write_post_deploy_report`, executing `_write_post_deploy_report(ctx, prev)`. The step needs no model, no tool, no subprocess and no network, so subscription mode and API mode behave identically. It returns `status="ok"` in every case (op3); it never returns an error and never raises.

- **op1, deterministic report.** Add the named pure helper (§1aa) `_build_report_text(question, completed_phases, telemetry_digest, artifacts) -> str`, where `artifacts` maps `spec`, `review`, `fix` to `PRESENT` / `MISSING` and `satisfaction` to `PRESENT` / `MISSING` / `NOT_ASSESSED` (existing `_satisfaction_state`, existing constants). It renders exactly this layout, with `\n` line ends and a trailing newline:

  ```
  # Post-Deploy Report

  ## Final Checkpoint
  Done: <first non-empty line of the feature request, whitespace-collapsed, at most 100 chars>   (line omitted when the request is empty)
  Files: not assessed (deterministic report; see `git diff --stat`)
  Review: <review state>; satisfaction: <satisfaction state>
  Docs: See /ship cascade.
  Next: manual test / PR / done

  ## Completed Phases
  - <phase>                                  (one per `_collect_completed_phases`; "none recorded" when empty)

  ## Telemetry
  <the `_telemetry_digest` text>               (section omitted when the digest is empty)

  ## Artifacts
  - spec: PRESENT|MISSING
  - review: PRESENT|MISSING
  - fix: PRESENT|MISSING
  - satisfaction: PRESENT|MISSING|NOT_ASSESSED

  ## Concerns
  - <one bullet per gap: "<name> not assessed: <relpath> is MISSING" or "... is NOT_ASSESSED">   (`none` when no gap)
  ```

  The `Done:` line keeps the contract of `_parse_report_summary` (first `^\s*Done:\s*(.+?)\s*$` match, 100-char cut), so PR titles keep working. A review/fix/satisfaction doc that is missing is "not assessed", never an error. The phrase `not assessed` (lowercase) appears in the `Files:` line and in each gap bullet.
- **op2, step.** `_write_post_deploy_report(ctx, prev)`: resolve the scratchpad with the existing `_resolve_scratchpad`; compute the four artifact states from `SPEC_DOC_RELPATH`, `REVIEW_DOC_RELPATH`, `FIX_DOC_RELPATH`, `SATISFACTION_DOC_RELPATH`; call `_collect_completed_phases(ctx)` and `_telemetry_digest(ctx)`; render with `_build_report_text(ctx.question, ...)`; `atomic_write` it to `<scratchpad>/post-deploy/post-deploy-report.md` (creating the directory); emit `synthesize_report_written` `{"doc_path": <str>, "report_bytes": <int>, "gaps": [<names>]}` through the existing `_emit_safe`. The result is `StepResult(status="ok", step_name="write_post_deploy_report", data={"report_doc_path", "spec_path", "review_doc_path", "fix_doc_path", "satisfaction_doc_path", "report_bytes_written", "report_written": True, "artifact_states": {...}, "completed_phases": [...]}, duration_ms=0)`. Keys `synthesizer_status` and `structured_verdict` are gone.
- **op3, degrade, do not fail.** If the scratchpad cannot be resolved (`ValueError`) or the write raises `OSError`, the step emits `post_deploy_report_skipped` `{"reason": "no_scratchpad" | "write_failed", "error": <str>}` and returns `status="ok"` with `report_written: False` and no `report_bytes_written`. Phase 8 already falls back to the commit subject and the cleanup report when the file is missing. No new error code is introduced.
- **op4, deletions in `phase_7_synthesize.py`.** Delete `_build_synthesizer_prompt`, `_invoke_synthesizer_llm`, `_write_synthesizer_artifact`, `_SYNTHESIZER_STABLE_PREFIX`, `_parse_synthesizer_status`, `_parse_synthesizer_structured`, `_last_marker_wins`, `_parse_files_line`, `_emit_synthesize_disk_truth_telemetry`, `_context_gap_paths`, `_needs_context_error`, `_read_first_block`, `_resolve_model`, `_resolve_command`, `_default_model`, `_default_llm_command`, `DEFAULT_LLM_COMMAND`, `DEFAULT_SYNTHESIZER_TIMEOUT_SEC`, `_resolve_synthesizer_timeout_sec`, the `STATUS_*` constants, and every import that is left unused (`invoke_llm_subprocess`, `get_claude_fallback`, anti_hallucination helper, `disk_truth` names, `last_line_anchored_marker`, `phase_workflows_common` names, `_resolve_worktree_root`, `timeout_policy` names, `re`, `sys`). Keep `_emit_safe`, `_resolve_scratchpad`, `_telemetry_digest`, `_collect_completed_phases`, `_completed_phase_digest`, `_satisfaction_state`, the `*_DOC_RELPATH` and `SATISFACTION_*` constants. Replace the module docstring with a description of the one step.
- **op5, retired config.** Delete the `synthesize.llm` entry from `lib/timeout_policy.py`; delete `SynthesizerVerdict` from `lib/plugins/disk_truth/schema.py` and `__init__.py` (docstring list included); delete the `phase_7_synthesize:` entry from `lib/plugins/anti_hallucination/config.yaml`; delete the phase-7 line from `security/except-pass-allowlist.txt` (only if no `except ...: pass` remains in the file; the new code has none); delete the retired `phase_7_synthesize.py::...` keys from `conformance/class_i_inventory.json` (the `_satisfaction_state::read_text#0` entry is kept, with its note reworded to "rendered into the report"); delete the phase-7 entries of the bd#119 / bd#141 / bd#150 role-template and injection tables if they live in prod code (GREEN greps `phase_7_synthesize` across `engine_py/bytedigger_engine` for leftovers).
- **op6, error codes and org keys.** Delete `E_SYNTHESIZER_BLOCKED`, `E_SYNTHESIZER_NEEDS_CONTEXT`, `E_SYNTHESIZER_NO_MARKER` from `error_codes.py` and from both `ERROR_CODES.md` copies (the whole `## E_SYNTHESIZER` section goes with them; the copies stay byte-identical). The org keys `synthesizer_model`, `synthesizer_llm_command` and `synthesizer_llm_timeout_sec` become ignored: when `ctx.org_config` sets any of them, the report step emits one `synthesizer_config_ignored` `{"keys": [<sorted present keys>]}` event per run and carries on. A pre-upgrade config is never an error. `docs/configuration.md` loses `synthesizer_model` from the override-key list and gains it in the "accepted but ignored" sentence.
- **op7, md flow, agent, gate.**
  - Delete `agents/synthesizer.md`.
  - `phases/phase-7-synthesize.md`: replace "Actions" step 1 (the Haiku launch and the `learnings-raw.md` verification, lines 46-48) with one line saying the engine writes `post-deploy/post-deploy-report.md` from the event log (no agent, no deliverable to verify); delete "After synthesizer returns, extract learnings" (step 3, lines 59-64); the "WORKER AGENT CONSTRAINTS" block and the rest of the file stay. The AUTONOMOUS/SUPERVISED flow keeps its meaning with "log the report" instead of "log Haiku summary".
  - `commands/build.md`: 7.1 summary line becomes "the engine writes the post-deploy report"; delete the 7.1b line.
  - `scripts/phase-deliverables.tsv`: delete the `7 scratch_file reviews/learnings-raw.md` row (the header comment keeps listing `scratch_file` as a kind because the parsers still implement it). Rewrite the two comment lines that name the "bd#127 synthesizer deliverable" in `build-gate.sh` (`:370`) and `build-phase-gate.ts` (`:684`) to "The review result comes from the table." No other hunk in those two scripts.
- **op8, docs.** `docs/plugin.md` (`:217` agent table row, `:293` learnings sentence) and `docs/article.md:163`: say the post-deploy report is deterministic and that learnings are no longer extracted automatically. CHANGELOG `[Unreleased]`: a Removed bullet (synthesizer LLM step, agent, codes, timeout key, org keys, `SynthesizerVerdict`, `learnings-raw.md` deliverable and its soft gate) and an Added bullet (deterministic report, events `synthesize_report_written`, `post_deploy_report_skipped`, `synthesizer_config_ignored`).

## §3 Acceptance criteria. RED file: `engine_py/tests/test_bd89_p3c_deterministic_synthesize_report.py`

Real `EventLog` and real scratchpad dirs on `tmp_path`, no mocks of the report builder or writer (§1l). `llm_subprocess.invoke_llm_subprocess` and `subprocess.run` are patched to raise in the AC that proves independence. No other stubbing.

- **AC1** `phase_7_synthesize_workflow().name == "phase_7_synthesize"`; it has exactly one step, named `write_post_deploy_report`; the workflow is still registered (`workflows.register_all` / the registry lookup used by `test_bd180_suite_flat_workflow_names_fence` finds `phase_7_synthesize`).
- **AC2** (side effect, §1l) A scratchpad with all four docs present (satisfaction without the NOT_ASSESSED marker) and `ctx.question = "Add retry to the sync job\nmore detail"`. Running the step writes `<scratchpad>/post-deploy/post-deploy-report.md`. The file starts with `# Post-Deploy Report`, contains `## Final Checkpoint`, `Done: Add retry to the sync job`, `## Artifacts` with the four `PRESENT` lines, and `## Concerns` followed by `none`. The result is `ok`, `data["report_written"] is True`, `data["report_bytes_written"] == len(file bytes)`.
- **AC3** (degrade) A scratchpad with no spec, review, fix or satisfaction doc: the report is still written, `ok`, `error_code is None`; the text contains `not assessed` and the four artifact lines read `MISSING`; `## Concerns` lists one bullet per missing doc (4 bullets).
- **AC4** (NOT_ASSESSED) The satisfaction doc contains `SATISFACTION: NOT_ASSESSED`: its Artifacts line reads `NOT_ASSESSED` (not `PRESENT`), the `Review:` line says `satisfaction: NOT_ASSESSED`, and Concerns names `reviews/build-satisfaction.md` as not assessed. The report never contains a score.
- **AC5** (no model, no tool) With `invoke_llm_subprocess`, `subprocess.run`, `subprocess.Popen` and `os.system` patched to raise, the step still returns `ok` and writes the file. The two `bytedigger_engine.llm_subprocess` / `subprocess` names are also absent from the module source (`inspect.getsource`).
- **AC6** (event log) The log holds `workflow_finished` events for `phase_5_implement`, `phase_6_review` (twice) and `phase_7_synthesize`, with `include_telemetry_digest` unset. The report's `## Completed Phases` lists `phase_5_implement` then `phase_6_review` once each, in first-seen order, and not `phase_7_synthesize`. With `include_telemetry_digest=True` and one `subprocess_exited` event carrying `cost_usd`, a `## Telemetry` section appears; without the option it does not.
- **AC7** (phase 8 contract, GUARD-shaped but new) Feeding the written report text to `phase_8_post_deploy._parse_report_summary` returns the first line of the request, cut to 100 chars for a 140-char request line. A request with no text produces no `Done:` line and `_parse_report_summary` returns `""`.
- **AC8** (degrade on bad scratchpad) With no `scratchpad_dir` in `org_config` the step returns `ok`, `report_written is False`, emits one `post_deploy_report_skipped` event with `reason == "no_scratchpad"`, and does not raise. With a scratchpad whose `post-deploy` path is a regular file (write fails) the reason is `write_failed`. Neither result carries an `error_code`.
- **AC9** (ignored org keys) With `org_config` containing `synthesizer_model: "x"` and `synthesizer_llm_command: ["y"]`, the step is `ok` and the log has exactly one `synthesizer_config_ignored` event whose `keys == ["synthesizer_llm_command", "synthesizer_model"]`. Without those keys, no such event.
- **AC10** (retired codes) None of `E_SYNTHESIZER_BLOCKED`, `E_SYNTHESIZER_NEEDS_CONTEXT`, `E_SYNTHESIZER_NO_MARKER` is in `error_codes.ERROR_CODES` or in either `ERROR_CODES.md`; the two `ERROR_CODES.md` files are byte-identical; `python3 -m bytedigger_engine.error_codes --check` exits 0.
- **AC11** (retired timeout and schema) `"synthesize.llm"` is not in `timeout_policy.DEFAULT_POLICY`; `SynthesizerVerdict` is not importable from `bytedigger_engine.lib.plugins.disk_truth` nor present in its `schema.py`; `phase_7_synthesize.py` source contains none of `_build_synthesizer_prompt`, `_invoke_synthesizer_llm`, `E_SYNTHESIZER`, `SYNTHESIZER_STABLE_PREFIX`, `synthesizer_model` (except inside the string used by AC9's ignored-key tuple, which the test allows by checking only the `def`/identifier forms: the three function names, the constant and the code strings).
- **AC12** (agent and md) `agents/synthesizer.md` does not exist. `phases/phase-7-synthesize.md` and `commands/build.md` contain neither `learnings-raw` nor `agents/synthesizer.md` nor `7.1b`, nor `learning-store.sh extract`. `phases/phase-7-synthesize.md` still contains `## State Cleanup` and `## 7.5 SHIP Protocol` (GUARD).
- **AC13** (gate soft check removed) `scripts/phase-deliverables.tsv` has no row mentioning `learnings-raw`, and keeps its other 9 rows. Running `scripts/build-gate.sh` phase 7 over a fixture `build-state.yaml` with `review_complete: pass`, a scratchpad with no `reviews/learnings-raw.md` and `learning_backend: file` exits 0 with no `learnings-raw` text on stdout/stderr (bash gate). The TS twin is covered by the retargeted `worker-deliverables.test.ts` (local-only; CI does not run `bun test`).
- **AC14** (GUARD) `learning-store.sh extract` with no `reviews/learnings-raw.md` exits 0 and writes `learnings_extracted: 0` to `build-state.yaml`, for both `file` and `sqlite` backends (the degrade the MGR decision relies on).
- **AC15** (GUARD) `python3 -m bytedigger_engine.conformance.class_i_lint` exits 0; `python3 -m compileall -q engine_py/bytedigger_engine` exits 0; `config.yaml` of anti_hallucination has no `phase_7_synthesize` key and still parses.
- **AC16** (GUARD) `engine_py/tests/test_gh1124_ship_pr_title.py` is untouched by this slice's edits and stays green (phase 8 PR title/body contract).

### §1w op <-> AC map

op1 -> AC2, AC3, AC4, AC6, AC7 · op2 -> AC1, AC2, AC6 · op3 -> AC8 · op4 -> AC5, AC11 · op5 -> AC11, AC15 · op6 -> AC9, AC10 · op7 -> AC12, AC13, AC14 · op8 -> none (doc).

### §3 expected-red summary

Before GREEN, AC1-AC13 are red (AC1 on the step count, AC2-AC9 because the module still builds a prompt and calls the model, AC10-AC13 on presence checks). AC14, AC15, AC16 are green (a GUARD that is red before GREEN is a RED bug). AC12's two GUARD sub-asserts (`## State Cleanup`, `## 7.5 SHIP Protocol`) are green before GREEN. The sibling reds expected from §5 are listed by file and test in the RED commit message.

## §4 Out of scope (§1v: files and behaviours NOT in this PR)

- `hooks/worker_write_guard.py` `ROLE_DIR = {"synthesizer": "reviews"}`, `hooks/worker-write-guard.sh`, `docs/security.md` and `tests/test_worker_write_guard.py` / `test_worker_write_bash.py`: the guard's `synthesizer` role becomes unreachable, but removing it is a separate hook change with 99 test mentions. Follow-up.
- `scripts/learning-store.sh`, `scripts/learning-store-sqlite.sh`, `scripts/learnings_parse.py` and their bats suites stay as they are. They already degrade when `learnings-raw.md` is absent (AC14). With no producer left, automatic learning extraction is now dead; reviving it (for example from review findings) is a follow-up, not this PR.
- `phase_8_post_deploy.py` is unchanged (AC7, AC16 guard its contract). Only the word "synthesizer" in its comments stays; renaming is cosmetic.
- `derive_state.py` docstring wording ("so the synthesizer can read ...") and `lib/interpreter.py:27` prose are left alone.
- Phase 6 satisfaction evaluators, the reviewer path and the decorrelated verifier (p3b1c / p3b2). `bytedigger.json`, `docs/configuration.md` lines other than 158 (p3b1c / p3b2).
- No new learnings generation, no `git diff` shelling in phase 7 (the `Files:` line says `not assessed`).

## §5 Scope list (§1a sibling-test audit). RED edits these; GREEN treats tests as read-only (§1s)

Candidate set: every test that mentions `phase_7_synthesize`, `_build_synthesizer_prompt`, `_invoke_synthesizer_llm`, `_write_synthesizer_artifact`, `_parse_synthesizer_*`, `SynthesizerVerdict`, `E_SYNTHESIZER_*`, `synthesize.llm`, `synthesizer_*` keys, `learnings-raw.md` (gate side), `agents/synthesizer.md`, or a 3-step phase-7 pin. The RED author greps again on its HEAD and applies these rules.

1. **Delete whole (the file tests only the removed LLM path).** `test_phase_7_synthesizer_structured_verdict.py`, `test_phase_7_step8_disk_truth.py`, `test_phase_7_step8_disk_truth_subset.py`, `test_phase_7_step8_disk_truth_error.py` (disk-truth telemetry), `test_phase_7_synthesize_W11.py` and `test_phase_7_synthesize.py` after the RED author confirms per test that nothing in them covers kept code. Tests of kept helpers (`_telemetry_digest`, `_collect_completed_phases`, `_completed_phase_digest`, `_satisfaction_state`) are moved into the new RED file or a renamed sibling, not lost; each retired test gets a one-line reason in the RED commit message.
2. **Re-point to the new step.** `test_phase_7_resume_context.py` (digest behaviour stays, the prompt-embedding asserts become report-text asserts), `test_gh1626b_satisfaction_on_abort.py` (NOT_ASSESSED must reach the report as in AC4; drop the `E_SYNTHESIZER_NEEDS_CONTEXT` asserts; the abort-to-phase-7 integration at `:675` registers the one-step workflow), `test_io_utils.py:72-100` (atomic_write import and use now go through `_write_post_deploy_report`).
3. **Drop the phase-7 entry from per-site tables.** `test_bd141_p4d_role_template_injections.py` (`:387-403`, `:758`), `test_bd150_class_i_inventory.py` (`:286-305`, `:537-547`, AC8 `f2_synthesizer` id), `test_bd119_role_template.py` (`:802`, `:1041`), `test_gh705_callsite_stable_prefix.py` (Site 2), `test_F9F7E4FD_out_of_role_injection.py` (T08), `test_llm_subprocess_allowed_tools.py` (`:529-532`), `test_EEFD480F_waveB_marker_line.py` (ACB-P3 `_parse_synthesizer_status`), `test_timeout_policy_GH285.py:41` and the `test_timeout_policy_callsites_GH285C2` family (`synthesize.llm` row). Expected counts in those tables drop by one; no assertion about other sites may change.
4. **Name-list pins that keep `phase_7_synthesize`.** `test_bd180_suite_flat_workflow_names_fence.py`, `test_bd89_p1_devops_dropped.py:89`, `test_bd89_p2a_phases_1_4_dropped.py:119`, `test_bd89_p2b_one_spec_one_review_path.py:92`: audit only; the workflow name is unchanged.
5. **`learnings-raw` gate tests.**
   - `tests/test_worker_deliverables.py`: delete the agent-frontmatter and contract tests (AC1-AC3 groups, `test_ac2_synthesizer_uses_scratchpad_dir_placeholder`, `test_ac3_phase7_*`) and the gate tests that demand `learnings-raw.md` (`test_ac5_*`, `test_ac5c_*`, `test_c2_gate7_*`, `test_c3_gate7_*` where they assert the deliverable). KEEP: AC6/C6/C7 parse-error tests for both learning-store backends, the AC7 CI-wiring test, `test_c2_gate4_*` (explorer/architect gates) and `test_c11_gate_survives_gnu_stat`. Where a kept gate test used `learnings-raw.md` only as the phase-7 fixture, rewrite it so phase 7 passes without the file.
   - `scripts/ts/__tests__/worker-deliverables.test.ts`: remove the "AC5 — phase 7 gate: learnings-raw.md deliverable" describe; keep "Rev 3 — C2 spaced scratchpad path" with the phase-7 case retargeted to the review-complete check.
   - `tests/test_bd136_gate_counter_table_parser.py` (`:181`, `:229`, `:339`, `:409-438`, `:875-911`): the table-parser tests that use the `7 scratch_file reviews/learnings-raw.md` row move to a synthetic table fixture written by the test (the parser keeps `scratch_file`); tests that assert the real tsv still contains the row become "row absent" asserts.
   - `tests/build-gate.bats`, `tests/learning-store*.bats`, `tests/post-deploy*.bats`: audit; learning-store suites must stay unchanged and green (AC14).
6. **Phase 8 guard.** `test_gh1124_ship_pr_title.py`, `test_bd131_ship.py`, `test_GH1399_advisory_format_terminal.py`, `test_incident_ledger.py`, `test_bd117a_readiness.py`, `test_GH1471_inject_path_and_visibility.py`: audit only; they mention the word or the report file, not the removed code. `test_gh1626c_worker_interpreter.py`: prose mentions only, unchanged. Fixture `engine_py/tests/fixtures/sibling_coupling/prod/mixed_literal.py` and `test_sibling_coupling` users: audit; unchanged unless they import a removed name.

Verify scope (§1r):
- the RED file and every file in §5 (baseline recorded on `1e391ed` before RED; post-GREEN count is the baseline minus the retired tests, with 0 failed);
- `engine_py/tests/*phase_7*`, `*error_code*`, `*class_i*`, `*conformance*`, `*timeout_policy*`, `*gh1124*`;
- `tests/test_bd136_gate_counter_table_parser.py`, `tests/test_worker_deliverables.py`, the bats files named in §5.5 (when `bats` is installed), and `bun test scripts/ts/__tests__/worker-deliverables.test.ts` (local);
- `python3 -m compileall -q engine_py/bytedigger_engine`, `python3 -m bytedigger_engine.error_codes --check`, `python3 -m bytedigger_engine.conformance.class_i_lint`, and the English-only tree check (no Cyrillic in any tracked file or commit subject).

The full suite is CI only.

## §6 Resolved (orchestrator, 2026-10-03, AUTO-DECISION)

- **One step, workflow name kept.** Why: `workflows/__init__.py`, `core_manifest.json` and the orchestrator calls use the name `phase_7_synthesize`; renaming it churns every caller for no behaviour gain.
- **The step never errors.** Why: MGR principle "degrade, don't crash", and the report is an aid, not a gate. Phase 8 already has fallbacks for a missing report, so a write failure costs a worse PR title, not a failed build.
- **`Files:` is `not assessed`, not a `git diff` call.** Why: the step must have no tool dependency and must read the event log only; `git` is a tool. The line keeps the section shape the old report had.
- **`Done:` comes from the request text, not from a summary.** Why: it is the only deterministic one-line description of the build; it keeps `_parse_report_summary` and the PR title path working. An empty request omits the line so phase 8 falls back to the commit subject.
- **Learnings are dropped, not re-derived.** Why: option (b) of the P3 inventory. Lessons are an LLM product; deriving them from findings is new scope. The consumers already no-op (AC14).
- **Retired org keys are ignored with a one-time event.** Why: matches the precedent for `architect_model` and friends in `docs/configuration.md`; an existing config must not break on upgrade.
- **The hook's `synthesizer` role stays.** Why: out of the slice (§4); it is harmless without the agent.

### GAP list (not ported)

- Automatic learning extraction has no producer now. Follow-up: derive entries from findings/fix events, or retire `learning-store*.sh` and `learnings_parse.py`.
- `hooks/worker_write_guard.py` `ROLE_DIR` synthesizer entry and its tests.
