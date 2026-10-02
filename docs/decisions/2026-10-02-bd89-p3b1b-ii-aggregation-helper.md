# bd#89 P3b1b-ii: fold phase-6 aggregation into `write_review_artifact`, read a fixed `role-composite.md`, drop the fanout banner and `E_NO_ROLE_FILES`

**Status: FROZEN r1** · **Tier:** 3 (engine prod `.py`, Option D) · **Class:** PROCESS (dead-path removal after bd#89 P3b1; the per-role fan-in has had one input since #198) ·
**Chokepoint:** `_aggregate_review_findings` (`engine_py/bytedigger_engine/workflows/phase_6_review.py:1530`). It is the only prod reader of `<scratchpad>/reviews/role-*.md` (`:1558`), the only raiser of `E_NO_ROLE_FILES` (`:1567`) and the only builder of the `## Fanout` banner (`:1796-1806`). Its only consumer is `_write_review_artifact` (`:2099`, through `prev.data`). Changing what it reads and calling it from the write step removes the glob, the code, the banner and the step together.
**Side of the seam (decision 2026-07-26 §7.1):** engine, plus `ERROR_CODES.md` (both copies) and CHANGELOG. No orchestrator-flow md, no gate script, no TS.
**Base:** `origin/main` `f636929` (P3b1b-i #205 merged as `44c8994`; #204 on top). **Source:** bd#89 option A (BLOCKED-DECISION comment 5956226542). This is the second half of the P3b1b split. Template: P3b1b-i spec `2026-10-02-bd89-p3b1b-i-straggler-machinery.md`.

## §0 Scope size

- 1 prod file: `workflows/phase_6_review.py`, plus a one-line deletion in `error_codes.py`.
- 3 docs: `engine_py/ERROR_CODES.md`, `engine_py/bytedigger_engine/ERROR_CODES.md` (byte-identical pair) and CHANGELOG.
- 1 RED file: `engine_py/tests/test_bd89_p3b1b_ii_aggregation_helper.py`.
- Up to 30 sibling test files audited (§5). One deletion is planned: `test_e8433b4e_aggregator_partial_floor.py`.
- Phase 6 goes from 21 steps to 20.

## §1 Problem (measured on `f636929`)

- `len(phase_6_review_workflow().steps) == 21` (live, 2026-10-02). Step 3 is `StepContract("aggregate_review_findings", skip_on_error=True)` (`:5861-5865`), sitting between `invoke_review_llm` and `write_review_artifact`.
- Since #198, `_select_reviewers` returns exactly one reviewer, `composite` (`:449`, `:455-456`). That reviewer writes `role-composite.md`; the prompt at `:749` and `SINGLE_REVIEW_FRAMING_TEMPLATE` (`lib/plugins/review_schema/canonical.py:44`) both name the file. The aggregator still globs `role-*.md` (`:1558`), so any leftover `role-<slug>.md` gets merged into the review.
- The `## Fanout` banner (`expected: / observed: / missing:`) always reads `expected: 1`. It reports a fan-out that no longer exists. `_extract_expected_slugs` (`:1116`) and the `_review_plan` call (`:1580`) exist only to feed it.
- `E_NO_ROLE_FILES` (`error_codes.py:137`, `ERROR_CODES.md:204` x2) is raised only at `:1567`. Thanks to `skip_on_error=True`, its only effect is that `write_review_artifact` falls back to stdout/disk, and the writer already does that whenever `aggregated_content` is falsy (`:2108-2113`).
- Baseline of the 30 siblings in §5 on `f636929`: **883 passed, 0 failed**. `error_codes --check` OK; `conformance.class_i_lint` rc 0.

## §2 Design: the aggregator becomes a helper called by the write step

The function bodies stay where they are and keep their signatures. The step list stops calling the aggregator, and the write step calls it instead.

- **op1, fixed input.** Add `_COMPOSITE_ROLE_FILE = "role-composite.md"` next to `_ROW_COMPOSITE_REVIEWER`. `_aggregate_review_findings` reads only `reviews_dir / _COMPOSITE_ROLE_FILE`. When the file is missing, it emits `role_report_missing` `{"phase": "phase_6_review", "path": <str>}` and returns `StepResult(status="ok", data={**forwarded, "aggregated_content": None}, step_name="aggregate_review_findings")`, with no error code. The per-file loop, parsing, linting, quote verification and audit stay as they are; they now run on a list of at most one file. The `E_MISSING_SCRATCHPAD` branch is unchanged.
- **op2, banner.** Delete the `## Fanout` block (`:1796-1806`), the `_review_plan` / `_extract_expected_slugs` lookup in the aggregator (`:1571-1586`), the `_extract_expected_slugs` def (`:1116`), and the `expected_reviewers` / `observed_role_count` return keys. `_slug_from_role_filename` stays because the loop still uses it.
- **op3, fold the step.** Add the named helper (§1aa) `_aggregate_and_write_review_artifact(ctx, prev) -> StepResult`:
  - `agg = _aggregate_review_findings(ctx, prev)`.
  - If `agg.status == "error"`, emit `review_aggregation_error` `{"phase": "phase_6_review", "error_code": agg.error_code, "error": agg.error}`.
  - `return _write_review_artifact(ctx, agg)`.

  This matches the engine's `skip_on_error` hand-off today, where `prev = result` (`engine.py:855`). Delete the `aggregate_review_findings` `StepContract`. The `write_review_artifact` step's execute becomes the helper, and `RetryPolicy(max_retries=1)` stays.
- **op4, stale guard and comments.** The pre-invoke stale-file guard in `_invoke_review_llm` (`:1053-1060`) unlinks only `reviews_dir / _COMPOSITE_ROLE_FILE`. There is no `role-*.md` glob left in prod. Rewrite the comments at `:1028`, `:1078-1090`, `:1533-1539` and `:2108-2112` and the step block at `:5860`. Replace the stale module docstring list ("Steps (10)", `:70-90`) with the real 20 step names.
- **op5, error code.** Delete `E_NO_ROLE_FILES` from `error_codes.py` and from both `ERROR_CODES.md` copies, keeping them byte-identical.
- **op6, CHANGELOG.** Add an `[Unreleased]` / Removed bullet covering the aggregation step (21 -> 20), the role glob, the fanout banner and `E_NO_ROLE_FILES`. A second bullet covers the new events `role_report_missing` and `review_aggregation_error`.

## §3 Acceptance criteria. RED file: `engine_py/tests/test_bd89_p3b1b_ii_aggregation_helper.py`

Real `EventLog` on `tmp_path`, real scratchpad dirs, and no mocks of the aggregator or writer, per §1l. Only the LLM boundary may be stubbed, and no AC needs it.

- **AC1** `phase_6_review_workflow().steps` has 20 entries. The names equal the base list minus `aggregate_review_findings`, in order, so index 2 is `write_review_artifact`.
- **AC2** The `write_review_artifact` step's execute `is p6._aggregate_and_write_review_artifact`, its retry policy still has `max_retries == 1`, and no step named `aggregate_review_findings` exists.
- **AC3** (side-effect, §1l) A scratchpad holds `reviews/role-composite.md` with one finding whose quote matches a real file under the target root. Running the write step's execute writes the file at `doc_path`, and that file contains `# Composite Review` and the finding. The result is `ok`.
- **AC4** `role-composite.md` sits next to a leftover `role-security.md` with a distinct marker string. The written doc contains the composite finding and not the marker, and `data["role_files"] == [str(<composite path>)]`.
- **AC5** Only `role-security.md` is present. The event log has one `role_report_missing` event whose `path` ends in `role-composite.md`. Neither the result nor the event log contains `E_NO_ROLE_FILES`. The doc is written through the stdout fallback from `prev.data["raw_response"]`.
- **AC6** Calling `_aggregate_review_findings(ctx, prev)` directly with no composite file returns `status == "ok"`, `error_code is None` and `data["aggregated_content"] is None`.
- **AC7** For AC3's input, `aggregated_content` contains none of `## Fanout`, `\nexpected:`, `\nobserved:` or `\nmissing:`, and `data` has neither `expected_reviewers` nor `observed_role_count`.
- **AC8** `"E_NO_ROLE_FILES"` appears neither in `error_codes.ERROR_CODES` nor in either `ERROR_CODES.md`, and `python3 -m bytedigger_engine.error_codes --check` exits 0.
- **AC9** With an unresolvable scratchpad, the helper emits one `review_aggregation_error` with `error_code == "E_MISSING_SCRATCHPAD"`. Its return value has the same `status` and `error_code` as `_write_review_artifact(ctx, _aggregate_review_findings(ctx, prev))`.
- **AC10** Checked as source text, `phase_6_review.py` contains no `glob("role-*.md")`, no `_extract_expected_slugs`, no `E_NO_ROLE_FILES` and no `## Fanout`.
- **AC11** (GUARD) `p6._COMPOSITE_ROLE_FILE == "role-composite.md"`, and that string appears in `SINGLE_REVIEW_FRAMING_TEMPLATE`, so the prompt and the reader agree.
- **AC12** (GUARD) `python3 -m bytedigger_engine.conformance.class_i_lint` exits 0, and `class_i_inventory.json` still has the key `workflows/phase_6_review.py::_aggregate_review_findings::read_text#0`.
- **AC13** (GUARD) Calling `_write_review_artifact(ctx, prev)` directly with `prev.data["aggregated_content"]` set still writes that content to `doc_path`. This keeps the signature that 39 direct test callers rely on.

### §1w op <-> AC map

op1 -> AC4, AC5, AC6, AC11, AC12 · op2 -> AC7 · op3 -> AC1, AC2, AC3, AC9, AC13 · op4 -> AC10 · op5 -> AC8 · op6 -> none (doc).

### §3 expected-red summary

Before GREEN, AC1-AC10 are red and AC11-AC13 are green. A GUARD that comes out red before GREEN is a RED bug. The sibling reds expected from §5 edits are listed in the RED commit message, by file and test.

## §4 Out of scope (§1v: files and behaviours NOT in this PR)

- `_select_reviewers`, `_review_plan` and `_ROW_COMPOSITE_REVIEWER` stay, because `_invoke_review_llm` dispatch uses them (`:746`).
- `_slug_from_role_filename` stays.
- `lib/plugins/review_schema/canonical.py` (the prompt template) is unchanged.
- `phases/phase-6-review.md` is unchanged; it has no mention of aggregation, role files or the banner.
- `class_i_inventory.json` is unchanged (AC12).
- `verify_findings*`, `build_fix_prompt` and the fix tail are unchanged.
- The `step_name="aggregate_review_findings"` label on the helper's internal `StepResult` stays. The label is internal, and test fixtures construct it.
- The multi-evaluator satisfaction step stays out, as the #89 follow-up.
- `straggler_cfg` / `abort` belong to #202.

## §5 Scope list (§1a sibling-test audit). RED edits these; GREEN treats tests as read-only (§1s)

The candidate set is every test file that mentions `_aggregate_review_findings`, `_write_review_artifact`, `role-<x>.md`, `E_NO_ROLE_FILES`, `## Fanout`, `aggregate_review_findings`, or a 21-step pin. That is 30 files (list in §5.6). The RED author reads each one. The rules below decide what happens to it.

1. **Step-count and step-name pins.** Change 21 to 20 and drop `aggregate_review_findings` from pinned lists.
   - `test_phase_6_mass_unverified_5F9817F6.py:334`
   - `test_gh379_decorr_verify.py:50`
   - `test_bd89_p3b1_single_reviewer_only.py:667-677` (`_PHASE6_STEPS`)
   - `test_bd141_p6_review_gate.py:35` (`PINNED_STEPS`)
   - `test_gh1591_fix_gate_boundary.py:85-86`: the step-exec assertion moves to `_aggregate_and_write_review_artifact`, and indices shift by one.
   - Audit `test_291189a0_phase6_step_factory.py`, `test_bd141_p6_start_gate.py` and `test_phase_6_review_W2.py` for index or name pins.
2. **Aggregator `StepContract` asserts.** These are `test_bd89_p3b1_single_reviewer_only.py:553-565` (no `resume_sentinel`, resume-file glob) and `:595` (`skip_on_error`). Retire them, or retarget them to the write step where the intent still applies (for example, "aggregation output is not resume-cached").
3. **`E_NO_ROLE_FILES` behaviour asserts.**
   - Covered: `test_906e37dc_review_findings_audit.py:437-459`, `test_bd139_single_reviewer.py:177,305,326`, `test_bd89_p3b1_single_reviewer_only.py:483,492,601`.
   - Rewrite to the AC6 contract: `ok`, `aggregated_content is None`, no `findings_audit`.
   - Catalog-presence anchors in `test_bd89_p3a_surgical_revise_dropped.py:804`, `test_bd89_p1_devops_dropped.py:203,210` and `test_bd89_p2a_phases_1_4_dropped.py:243,249` use it as "a live code". Swap the anchor to `E_MISSING_SCRATCHPAD`, which is still live.
4. **Banner asserts.**
   - Delete `test_e8433b4e_aggregator_partial_floor.py`. All 3 of its tests pin multi-role floor or banner semantics; the RED author confirms that per test before deleting.
   - `test_bd139_single_reviewer.py:287-288` and `test_bd89_p3b1_single_reviewer_only.py:451,548,652`: replace the `expected:` / `observed:` / `missing:` asserts with a banner-absent assert, or drop the line.
5. **Role fixtures.** Most direct callers of `_aggregate_review_findings` write `role-<slug>.md`.
   - Rename every fixture to `role-composite.md`.
   - Where one scenario writes two or more role files and the assertion concerns finding handling (quotes, severity headers, tags, backticks, suspect rate, audit counts), merge the bodies into one composite file and keep the intent.
   - Where the intent is inherently per-file, retire that test with a one-line reason in the RED commit message. Per-file means per-role sections, cross-file counts, per-file self-count, or leftover-merge such as `test_bd89_p3b1_single_reviewer_only.py:539` `test_ac8b_guard_leftover_role_files_aggregate`. Rewrite that one to "leftovers are ignored".
   - Watch `lint_role_report` self-count: merged bodies must restate one consistent count.
   - Files: `test_phase_6_FEB64BA8_backtick_strip`, `test_phase_6_1F39FB1A_soft_tag`, `test_phase_6_reviewer_suspect_rate_D3492E45`, `test_906e37dc_review_findings_audit`, `test_gh1591_fix_gate_boundary`, `test_phase_6_review_21792EE7`, `test_phase_6_verified_only_gate_65695203`, `test_bd139_single_reviewer`, `test_bd84_model_output_normalizer`, `test_bd89_p3b1_single_reviewer_only`, `test_gh970_severity_hdr_tolerant`, `test_3F5599A6_a2a3_residue`, `test_CD3EDB8C_phase6_cite_worktree_root`, `test_phase_6_fuzzy_citation_match_A37D4F04`.
6. **Expected unchanged; audit only.** These call `_write_review_artifact` directly, so AC13 guards their signature:
   - `test_GH1399_advisory_format_terminal`
   - `test_phase_6_review_return_discipline_CF838E6F`
   - `test_phase_6_stdout_fallback_verdict_4E0BAC38`
   - `test_F7830037_insession_review_normalize`
   - `test_phase_6_CA50885D_suspect_fail_open` (`inspect.getsource` of the aggregator)
   - `test_llm_subprocess_allowed_tools`

   Full 30-file candidate list: 291189a0, 3F5599A6, 906e37dc, bd139, bd141_p6_review_gate, bd141_p6_start_gate, bd84, bd89_p1, bd89_p2a, bd89_p3a, bd89_p3b1, CD3EDB8C, e8433b4e, F7830037, GH1399, gh1591, gh379, gh970, llm_subprocess_allowed_tools, 1F39FB1A, CA50885D, FEB64BA8, A37D4F04, 5F9817F6, 21792EE7, CF838E6F, W2, D3492E45, 4E0BAC38, 65695203.

Verify scope (§1r):
- the RED file;
- the 30 files above (baseline 883 passed / 0 failed; the post-GREEN count is that baseline minus retired tests, with 0 failed);
- every `tests/*phase_6*`, `*error_code*`, `*class_i*` and `*conformance*` file;
- `error_codes --check` and `compileall`.

The full suite is CI only.

## §6 Resolved (orchestrator, 2026-10-02, AUTO-DECISION)

- **A wrapper step, not inlining into `_write_review_artifact`.** Why: 49 direct `_aggregate_review_findings(ctx, prev)` calls and 39 direct `_write_review_artifact(ctx, prev)` calls in tests keep working. The class-I inventory key stays valid. The engine's `skip_on_error` hand-off is reproduced exactly: the writer gets the aggregator's result as `prev`.
- **A missing composite is `ok` plus a `role_report_missing` event, not a new error code.** Why: the old error never stopped the pipeline, because of `skip_on_error`. Its only observable effect was the event-log entry, which the new event replaces 1:1.
- **Retry re-runs aggregation.** `RetryPolicy(max_retries=1)` on the write step now covers the aggregation too, so on a retry the read-only aggregation runs twice and `review_findings_audit` is emitted twice. Accepted: retries are rare and the reader is pure. Not worth splitting the step.
- **The stale guard is narrowed to the fixed file.** Why: leftover legacy `role-<slug>.md` files are no longer read (AC4), so deleting them is unnecessary. Keeping a glob only for cleanup would leave a second `role-*.md` site in prod.

### GAP list (not ported)

- None. P3b1b-ii closes the P3b1b scope of #89 row 2, apart from the multi-evaluator follow-up.

## §7 Errata r1 (gate r1 APPROVED, `2026-10-02-bd89-p3b1b-ii-gate-r1.md`; not a re-freeze)

- **F1 (AC2).** `step()` always wraps `execute` in an `_execute` closure, so the literal `execute is helper` cannot hold while `RetryPolicy` is kept. AC2 is therefore checked through the closure: the `fn` cell is `_aggregate_and_write_review_artifact`, `retries.max_retries == 1`, and `execute.__name__ == "_execute"`. GREEN must register the write step via `step("write_review_artifact", _aggregate_and_write_review_artifact, retries=RetryPolicy(max_retries=1))`.
- **F2 (AC11).** AC11 is split in two. AC11a is a GUARD: `"role-composite.md"` appears in `SINGLE_REVIEW_FRAMING_TEMPLATE`, and it is green before GREEN. AC11b, `p6._COMPOSITE_ROLE_FILE == "role-composite.md"`, is red before GREEN. The expected-red line becomes: AC1-AC10 and AC11b are red, while AC11a, AC12 and AC13 are green.
- **A1 (§6 retry).** On a write-step retry, the aggregation re-emits every event it raises: `role_report_unreadable`, `role_report_malformed`, `role_report_missing`, `review_findings_audit`, the per-finding quote-verification events, `review_zero_findings_suspect` and the suspect-rate event, plus `review_aggregation_error` from the helper. The only prod consumer, `error_locus.py:68`, takes a max over `review_findings_audit`, so duplicates do not change its output.
- **A2 (op1).** "Missing" means `not (reviews_dir / _COMPOSITE_ROLE_FILE).is_file()`. This includes a `reviews/` dir that does not exist, and a directory sitting at that path. All of these cases emit `role_report_missing`.
- **A4 (op4).** `complexity` is still forwarded from `_invoke_review_llm` in `prev.data`. The aggregator no longer reads it, but downstream steps may, so GREEN keeps forwarding it. The comment at `:1028` says so.
