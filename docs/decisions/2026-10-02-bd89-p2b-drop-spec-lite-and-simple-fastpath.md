# bd#89 P2b: one spec path, one review path. Drop phase_45_spec_lite and phase_6_review_simple_fastpath from the engine

**Status: FROZEN r1 (awaiting Opus gate)** · **Tier:** 3 (engine prod `.py`, Option D) · **Class:** PROCESS (scope-of-build decision) ·
**Chokepoint:** the stage registry `register_all` (`engine_py/bytedigger_engine/workflows/__init__.py:25`). It is the only place an engine stage becomes reachable. The oracle set `conformance/oracle.py:61` (`ORACLE_WORKFLOWS`) is the second declaration of the same fact.
**Side of the seam (decision 2026-07-26 §7.1):** engine only. No md flow change here.
**Stacked on:** P1 (PR #176, `2026-10-02-bd89-p1-drop-devops-branches.md`). P1's RED AC1 frozen set is edited by this RED commit.
**Source:** bd#89 row 4 ("spec_lite is a second, weaker spec path"; acceptance: exactly ONE spec path) and row 2 (acceptance: one review path). Split plan in issue comment 5947880163: P2a = md flow (SIMPLE writes the spec inline in Phase 1; Phase 1 removal); P2b = this engine-only slice.

## §1 Problem (measured on the current P1 tree)

1. `register_all` registers two SIMPLE-only side paths (`workflows/__init__.py:12,15,26,29`): `phase_45_spec_lite` (1339 lines) and `phase_6_review_simple_fastpath` (95 lines). Nothing in `commands/`, `phases/`, `skills/`, `scripts/`, `templates/`, `agents/`, `hooks/` or `examples/` names either one (grep, zero hits). Only the registry, tests, the oracle set and docs do.
2. `phase_45_spec_lite` is a weaker twin of `phase_45_spec`. It has no `verify_spec_citations`, `cite_lint`, `coverage`, `scope_inverse`, `ac_dsl` or `reality` steps (compare the 21-step `phase_45_spec_workflow`, `phase_45_spec.py:5176-5200`, with the lite step list at `phase_45_spec_lite.py:1330-1339`). Its `_gate_on_review` (`:975`) downgrades a block-flagged spec to `SHIP_WITH_CONCERNS` and lets it through (39CAFF09, `:230-254`, `:1004`).
3. `phase_45_spec` already handles `complexity=SIMPLE`. `_default_min` is 0 for SIMPLE (`phase_45_spec.py:1732`); `build_class` defaults to `SIMPLE` in 13 places (`:1655,1737,1786,2703,...,5011`). After P2b the only path for SIMPLE is therefore the full one, with no new code.
4. `phase_6_review_simple_fastpath` (diff <= 10 lines, satisfaction only) reuses `phase_6_review` steps unchanged except its own `build_fastpath_stubs`. It is the second review path that row 2 forbids. It carries no behavior the full path lacks.
5. Dependents that would dangle or go dead once the two modules are gone (all verified on this tree):
   - `core_manifest.json:59,78`.
   - `conformance/oracle.py:61`: `ORACLE_WORKFLOWS = {"phase_45_spec", "phase_45_spec_lite"}`.
   - `lib/timeout_policy.py:35-41`: keys `spec_lite.writer`, `spec_lite.reviewer`. They are read only by lite (`phase_45_spec_lite.py:208-213`).
   - Description text and comments that name the lite workflow (see AC7 for the full list).
6. Measured as NOT affected:
   - **Error codes.** The 10 distinct `E_*` literals in the two modules are `E_INJECTION_MISSING`, `E_MISSING_PREV_DATA`, `E_REVIEW_FAILED`, `E_REVIEW_UNPARSEABLE`, `E_ROLE_TEMPLATE_INVALID`, `E_VALIDATION_RETRY` (all six have other users: `phase_45_spec.py`, `phase_5_implement.py`, `engine.py`, other phases) plus the regex fragments `E_SPEC_RELPATH`, `E_LINE_RE`, `E_WINDOW_LINES`, `E_RE`, which are variable names, not codes. **No code is deleted.** Only two descriptions are reworded (`E_INJECTION_MISSING`, `E_REVIEW_UNPARSEABLE`: `error_codes.py:103,201` and both `ERROR_CODES.md`).
   - **Flags.** Neither module reads an env var or a flag accessor. `flags_catalog.FLAGS` and `ROUTED_MODULES` hold no lite/fastpath entry. **No flag is deleted.**
   - **`skip_logic.py`.** The SIMPLE branch of `should_skip_phase` (GH531, `skip_logic.py:168-173`) serves the phase 1-4 self-skip path, not lite. `detect_frozen_spec` has a second caller, `phase_45_spec._step_detect_frozen_spec` (`:5113`). **`skip_logic.py` is unchanged.**
   - **`phase_1_discovery`.** Lite imports `phase_1_discovery._build_prompt` (`:88`); that import disappears with lite. The module stays (P2a removes it).
   - **Shared helpers.** `lib/spec_retry_cycle.py`, `lib/plugins/checklist_convergence`, `lib/run_allowlist`, `lib/verdict_parse` all have `phase_45_spec` callers; they stay (only docstrings change).

## §2 Design: delete, don't flag

Row 4 and row 2 both say one path. A kept-behind-a-flag lite path would create a new default-off flag, which row 7 forbids. Nothing is kept.

- **op1 (registry):** delete `workflows/phase_45_spec_lite.py` and `workflows/phase_6_review_simple_fastpath.py`, their imports and their `register` lines. `register_all` registers exactly the 14-name set in AC1.
- **op2 (oracle):** `ORACLE_WORKFLOWS = frozenset({"phase_45_spec"})`. `IMPLEMENTING_WORKFLOWS` unchanged. The SIMPLE-tier freeze now comes from `phase_45_spec`, which was already in the set.
- **op3 (timeout policy):** delete the two `spec_lite.*` keys from `DEFAULT_POLICY`. The `spec.writer` / `spec.reviewer` keys are untouched.
- **op4 (manifest):** remove the two entries from `core_manifest.json`.
- **op5 (text residue):** every prod `.py` comment/docstring/description that names `spec_lite`, `phase_45_spec_lite` or `simple_fastpath` is reworded or removed (AC7 lists them). `E_INJECTION_MISSING` and `E_REVIEW_UNPARSEABLE` descriptions drop the lite name in `error_codes.py` and in both `ERROR_CODES.md` copies, which stay byte-identical.
- **op6 (tests):** retire, re-point, or flag per §5. No behavior in the kept path changes.
- **op7 (docs):** `docs/configuration.md` (the role-template step list, `:268-279`) drops the lite and fastpath sentences. README and `docs/article.md` wording ("spec-lite lane", "review fastpath", "21 workflow modules") is fixed in the doc dance. `CHANGELOG.md` history and `docs/decisions/*` stay untouched. `conformance/*_SPEC.md` are frozen historical specs and stay untouched (`ORACLE_SPEC.md` §`[bd8:7]` still names the lite member; see open question 1).

Provider principles: P2b adds no LLM call and no provider dependency. It deletes the only second code path that picked models on its own (`get_claude_spec_writer` / `get_claude_spec_reviewer` calls in lite). SIMPLE builds now use the same model-resolution seam as FEATURE/COMPLEX, so a subscription CLI and an API key both work exactly as they do for the full path. Deterministic-first: SIMPLE specs now pass the full deterministic gates (cite prelint, cite lint, coverage, scope inverse, AC DSL, reality) before any LLM review. All new tests are deterministic and run with no `claude` on PATH and no API key (AC10).

## §3 Acceptance criteria. RED file: `engine_py/tests/test_bd89_p2b_one_spec_one_review_path.py`

Pre-GREEN expectation is stated per AC. A guard is green at RED on purpose and is labeled as such.

- **AC1 (registry, side-effect):** `register_all(fake_engine)` records exactly `{echo, phase_0_research, phase_05_inject, phase_1_discovery, phase_2_explore, phase_3_clarify, phase_4_architect, phase_45_spec, phase_5_implement, phase_5_integrity, phase_6_fix_integrity, phase_6_review, phase_7_synthesize, phase_8_post_deploy}` (14 names, no duplicates). **AC1b (dispatch):** with the real registry installed, asking the engine to run `phase_45_spec_lite` or `phase_6_review_simple_fastpath` fails with the engine's unknown-workflow error (the same path the P1 tests use for dropped stages), while `phase_45_spec` and `phase_6_review` resolve. Red before GREEN.
- **AC2 (files gone):** neither module file exists on disk. `importlib.util.find_spec("bytedigger_engine.workflows.phase_45_spec_lite")` and `...phase_6_review_simple_fastpath` return `None`. `core_manifest.json` `core_modules` lists neither. Generic invariant: every `core_modules` entry resolves to a file under `engine_py/bytedigger_engine/`, except the frozen already-dangling set the RED author measures at base (P1 r1 lesson: measure first, freeze the set, check `stale` before `dangling`). **Guard, green at RED:** no `flags_catalog.FLAGS` `module` value and no `ROUTED_MODULES` entry names either stem.
- **AC3 (oracle):** `oracle.ORACLE_WORKFLOWS == frozenset({"phase_45_spec"})` and `oracle.IMPLEMENTING_WORKFLOWS == frozenset({"phase_5_implement"})`. Red before GREEN.
- **AC4 (timeout keys):** `DEFAULT_POLICY` has no key that starts with `spec_lite.`; the `spec.writer` and `spec.reviewer` values are byte-equal to the base values (600 / 300,600,900 and the opus overrides, copied as literals from `test_timeout_policy_GH285.py`'s `PARITY_TABLE`). Red before GREEN.
- **AC5 (error codes kept, descriptions clean):** the six shared codes in §1.6 are all still keys of `error_codes.ERROR_CODES` (guards against over-deletion; green at RED). In `error_codes.ERROR_CODES`, in `engine_py/ERROR_CODES.md` and in `engine_py/bytedigger_engine/ERROR_CODES.md`, the description of `E_INJECTION_MISSING` and `E_REVIEW_UNPARSEABLE` contains neither `spec_lite` nor `phase_45_spec_lite`. The two `.md` files are still byte-identical. The description part is red before GREEN.
- **AC6 (no flag/skip_logic collateral, guard):** `skip_logic.should_skip_phase({"decision_doc": <frozen doc>, "complexity": "SIMPLE"})` still returns skip=True with the kill-switch on and skip=False with `HAL_FROZEN_SHORT_CIRCUIT=0`. Proves the GH531 SIMPLE relax is untouched. Green at RED, labeled guard.
- **AC7 (text residue, grep):** a case-sensitive search for `spec_lite|phase_45_spec_lite|simple_fastpath|spec-lite` returns nothing in `engine_py/bytedigger_engine/**/*.py`, `engine_py/ERROR_CODES.md` and `engine_py/bytedigger_engine/ERROR_CODES.md`. `conformance/*.md` (frozen specs) are excluded. This forces GREEN to edit exactly these places (§1v, nothing else):
  - `workflows/phase_45_spec.py:9,212,341,1247,1697` (comments/docstring);
  - `workflows/phase_workflows_common.py:20`; `workflows/phase_8_post_deploy.py:106`;
  - `contracts.py:358`; `llm_subprocess.py:1762`; `lib/spec_retry_cycle.py:2`;
  - `error_codes.py:103,201` and both `ERROR_CODES.md` (`:153,312`);
  - `conformance/oracle.py:61`, `lib/timeout_policy.py:35-41`, `workflows/__init__.py` (op1-op3 text).
- **AC8 (SIMPLE goes through the full path; §1l side-effect, guard + AC1b):** for a ctx with `org_config={"complexity": "SIMPLE", ...}` and a tmp project root:
  - (a) `phase_45_spec_workflow().steps` contains, in order, `verify_spec_cite_prelint`, `verify_spec_citations`, `verify_spec_cite_lint`, `verify_spec_scope_inverse`, `verify_spec_coverage`, `verify_spec_ac_dsl`, `verify_spec_reality`, `write_review_doc`, `gate_on_review`;
  - (b) the real `_verify_spec_cite_prelint(ctx, prev)` on a spec file that cites an unresolved bare symbol emits `spec_cite_prelint_result` with `unresolved_count >= 1` into a real event-log sink (not a mocked emitter) and returns a non-blocking `StepResult` with the symbol list;
  - (c) with the flag `HAL_SPEC_CITE_PRELINT_ENFORCE` on, the same call returns the recoverable `E_SPEC_CITE_PRELINT_RETRY` gate result for SIMPLE (the lite path never could). Fixture model: `test_GH681_cite_prelint_enforce.py`.
  This AC is a guard (the full path already supports SIMPLE). The red-before-GREEN discriminator for "SIMPLE has only the full path" is AC1b.
- **AC9 (test-corpus closure, count edits):** no file under `engine_py/tests/` other than the RED file references `phase_45_spec_lite`, `phase_6_review_simple_fastpath` or `spec_lite.`. `test_bd44_package_namespace.EXPECTED_WORKFLOWS == 14` (the console-script `--list` AC2 and entry-point AC11b use it). `test_bd89_p1_devops_dropped.FROZEN_REGISTRY` has the 14 names of AC1. `pytest --collect-only -q` reports zero collection errors (orchestrator step, §5).
- **AC10 (provider-agnostic):** the RED file's tests pass under `monkeypatch.delenv("ANTHROPIC_API_KEY")` with a PATH that has no `claude`. An autouse fixture sets this up (same fixture as P1 AC9).
- **AC11 (docs coupling):** `docs/configuration.md` contains neither `phase_6_review_simple_fastpath` nor `maybe_rewrite_simple_spec_prompt` (forced by the re-pointed `test_bd119_role_template.py::test_configuration_doc_documents_key`, needles at `:1477`). The `NOT_ASSESSED ... fast` sentence requirement (`:1481-1485`) is retired together with the fast-path sentence. Red before GREEN.

## §4 Out of scope (§1v)

- Phase 1-4 workflows and the `phase_1_discovery` removal (P2a). Spec-writing inline in Phase 1 for SIMPLE (md flow, P2a).
- `findings-*.md` gates, phase_6 `parallel` fan-out, decorrelated verifier, reviewer-count keys (P3). phase_7_synthesize and the surgical revise (P3).
- **Porting any lite-only behavior** into `phase_45_spec` (see GAP list; only filed as follow-ups).
- `conformance/*_SPEC.md` rewrites, README/article/CHANGELOG history (doc dance only for the README/article wording).
- `skip_logic.py` (unchanged, AC6), `lib/spec_retry_cycle.py` behavior, `lib/timeout_policy.py` resolution logic.

## §5 Scope list

Prod deletions:
- `engine_py/bytedigger_engine/workflows/phase_45_spec_lite.py`, `engine_py/bytedigger_engine/workflows/phase_6_review_simple_fastpath.py`

Prod edits (counts: 2 deleted, 14 edited incl. `.md`):
- `workflows/__init__.py`, `conformance/oracle.py`, `lib/timeout_policy.py`, `engine_py/core_manifest.json`
- text-only: `workflows/phase_45_spec.py`, `workflows/phase_workflows_common.py`, `workflows/phase_8_post_deploy.py`, `contracts.py`, `llm_subprocess.py`, `lib/spec_retry_cycle.py`, `error_codes.py`, `engine_py/ERROR_CODES.md`, `engine_py/bytedigger_engine/ERROR_CODES.md`
- docs: `docs/configuration.md` (AC11), README.md and `docs/article.md` wording (doc dance, not an AC)

Test retirements, whole files (7, sole subject is a dropped path; each verified on this tree). Twin means the kept-path test that covers the same behavior:
- `test_phase_6_review_simple_fastpath.py`: five tests are fastpath-only. **Before deleting, move `test_ac8_phase_6_review_workflow_step_count_unchanged` (asserts `len(phase_6_review_workflow().steps) == 21`) into a surviving phase_6 test file**; it guards the kept workflow. Satisfaction-step behavior is covered by `test_phase_6_mass_unverified_5F9817F6.py`, `test_phase_6_satisfaction_*`, `test_gh388_*`, `test_gh705_*`, `test_gh751_*`, `test_gh1065_*`, `test_gh1626b_*`, `test_4B9DF7D3_*`.
- `test_gh682_lite_cite_prelint.py`: AC3-AC7 twin in `test_gh675_specwriter_cite_grounding.py::test_ac3..ac7`; AC1/AC2 are lite-namespace and step-order checks; AC8/AC9 twin only partly in `test_GH681_cite_prelint_enforce.py::test_ac7_reentry_exhausted_path_is_idempotent`.
- `test_gh693_lite_cite_enforce.py`: L3, L6 twin in `test_GH681...::test_ac4_..., test_ac2_...`; L1 is lite loop structure; L2, L4, L5 see GAP-3.
- `test_phase_45_spec_lite_C094A1E1.py`: t14-t18 twin in `test_phase_45_spec_step6_w1_port.py` (schema block, restricted writer cycle 2, restricted reviewer cycle 2, per-finding verdicts, pass-with-unresolved coerced to revise).
- `test_phase_45_spec_lite_cycle1_verdict.py`: twin `test_phase_45_spec_cycle1_verdict.py` (same class names, incl. `TestTelemetry`).
- `test_phase_45_spec_lite_step5_structured_verdict.py`: ship/revise/drift/returned-verdict twin in `test_phase_45_spec_cycle1_verdict.py`; the rest see GAP-4.
- `test_phase_45_spec_lite_citation_autocorrect_32ED4070.py`: **no twin, GAP-1.**

Test edits, function level (RETIRE / RE-POINT; module-level lite imports at the marked lines break collection):
- `test_GH1674_injection_missing.py`: ac18 (`:986`) RETIRE (lite-vs-full parity; full twin in the same file). r18 (`:613`), ac21 (`:1113`), `test_gates_tolerate_a_ctx_with_no_scratchpad` (`:1378`): delete the lite half, keep the full-tier half. Comments at `:35,977,1003,1070,1134,1160` reworded.
- `test_phase_45_spec_complexity_timeouts_D1F51D7A.py`: ac6-ac12 RETIRE (twin ac1-ac5 for `phase_45_spec._resolve_review_timeout_sec`; ac12 also reads the deleted file from disk).
- `test_E6602155_frozen_spec_ingest.py`: remove module imports `:40,46`; ac10 (`:580`) RETIRE (the full path is covered by ac3-ac9, ac11, ac12); drop the `build_review_loop_contract` use at `:608,639` with it.
- `test_phase_45_spec_telemetry_D7B5BFB3.py`: remove imports `:28,30-38` (re-import `VERDICT_*`, `MAX_REVIEW_CYCLES`, `_truncate_findings` from `phase_45_spec` if it exports them; otherwise drop their use); drop `BOTH_PHASES` param `spec_lite` (`:51`); ac11 (`:421`) RE-POINT to the full module if its payload exists there, else RETIRE; ac12 (`:476`) RETIRE (asserts the lite-only `spec_lite_cycle2_abort`).
- `test_phase_45_verdict_synonyms_7f129bca.py`: remove import `:27`; ac7-ac10 RETIRE (twin ac1-ac6 in the same file).
- `test_bd8_l1_oracle.py`: `ORACLE_WORKFLOW_LITE` (`:110`) removed from the fixture loop (`:314`) and deleted; `test_ac1c_the_lite_oracle_workflow_also_freezes` (`:581`) RETIRE (`ac1`/`ac1b`/`ac1d` cover `phase_45_spec` freezing).
- `test_alpha0_BA456198.py`: remove import `:23`; the two lite telemetry tests (`:188,212`) RE-POINT to `phase_45_spec` (`_review_output_schema`, `_write_review_doc`; `telemetry_ctx` patch target and `phase` label change) unless the file already has the full-tier twin, then RETIRE.
- `test_bd85_retry_budgets.py`: `test_ac9f_fastpath_without_fix_step_stays_terminal` (`:420`) RE-POINT (phase label `phase_6_review`; it exercises the kept `_write_satisfaction_doc`); `test_ac18_lite_keeps_lint_terminal` (`:557`) RETIRE (GAP-3).
- `test_GH597_model_mix_phase45.py`: ac7, ac7b (`:154,159`) RE-POINT to `phase_45_spec` (full-tier twin ac may exist; then RETIRE).
- `test_phase_45_reviewer_cross_check_e94afd31.py`: import `:17` RE-POINT to `phase_45_spec._review_output_schema`. **The RED author verifies the cross-check directive text exists in the full schema; if not, GAP-5.**
- `test_io_utils.py`: `:89` and `:221` RE-POINT to `phase_45_spec` (it imports `atomic_write` at `:95` and has no `_atomic_write`).
- `test_run_allowlist_1DA29C33.py`: ac8 (`:462`) RETIRE (the full-tier gate test is the twin); docstring `:9`.
- `test_timeout_policy_GH285.py`: delete the two `spec_lite.*` rows of `PARITY_TABLE` (`:37-38`); AC4 asserts the absence.
- `test_F3A8F4FC_phase12_sonnet_downgrade.py`: drop the `phase_45_spec_lite.py` path from the lists at `:358` (ac9) and `:387` (ac10); comments `:333,353,373`.
- `test_llm_subprocess_hard_gate.py`: `test_hard_gate_chokepoint_phase_45_spec_lite_missing_gate_now_refused` (`:316`) RE-POINT to `phase_45_spec._invoke_review_llm` (it passes `hard_gate=True`, `phase_45_spec.py:4156`; this is the only chokepoint guard for the full reviewer, so do not retire).
- `test_llm_subprocess_allowed_tools.py`: delete the two lite rows (`:482-510`); the full-tier rows (`:461,472`) are the twins.
- `test_bd141_p4d_role_template_injections.py`: delete `_d_spec_lite_review`, `_d_spec_lite_free_rewrite` (`:323-346`), the `_DRIVERS` keys (`:418-419`), `_MATRIX` rows (`phase_45_spec_lite:481`, `:630`, at `:728,731`), the `_write_cycle1_review`/`_STRUCTURED_REVIEW` helpers only if no kept user remains. Counts: `len(_DRIVERS) == 17` (`:433`, **module-level assert, breaks collection**) becomes 15; the dispatch-call-count test `len(rows) == 21` (`:615`) becomes 19 (lite has exactly two direct `invoke_llm_subprocess(` calls: `:484,:637`) with docstring/comment `:20,30,614` updated ("22nd" becomes "20th"). `test_ac4b_restricted_writer_..` and `test_ac4b_restricted_reviewer_..` (`:658,671`): RE-POINT the reviewer guard to `phase_45_spec._build_review_prompt` (it sets `restricted_reviewer`, `phase_45_spec.py:3980,4009`); re-point the writer guard to `phase_45_spec._build_spec_prompt` if its restricted-writer data shape allows (`:1256`), else RETIRE with a note. Both guard "restricted branch carries no role", a kept behavior.
- `test_bd119_role_template.py`: remove imports `:61,67-68` (collection), `C_ROWS` rows `p45-lite-review`, `p45-lite-rewrite` (`:813-814`; 18 becomes 16 in `:9,975,1059` text), `_MODS` entry (`:1064`), the lite assert (`:988`) and the `fast = {...}` block (`:997-998`) in `test_role_template_consumers_match_step_table` (the `observed == expected` equality still holds because both sides drop the same rows), the two needles at `:1477` (AC11) with the `NOT_ASSESSED`/`fast` requirement (`:1481-1485`), and `test_phase6_fastpath_template_error_halts_without_stub` (`:1717`, RETIRE; the COMPLEX stub-on-abort test just above it is the twin).
- `test_bd44_package_namespace.py`: `EXPECTED_WORKFLOWS` 16 to 14 (`:48`); update the comment at `:40-46`.
- `test_bd89_p1_devops_dropped.py`: `FROZEN_REGISTRY` (`:87-92`) loses the two names (14); RED commit edit.
- Docstring-only, no edit needed: `test_contracts.py:474-485` (pure string fixture), `test_phase_45_spec.py`, `test_phase_45_spec_step6_w1_port.py`, `test_spec_retry_cycle.py`, `test_loop_runner.py`, `test_phase_45_spec_cycle1_verdict.py`. Reword if AC9's grep catches them.
- Checked, no coupling: `test_bd117b_companion_tune.py:1795`, `test_bd141_claim_evidence.py:409` (they assert specific manifest entries, not a full listing).

Run `pytest --collect-only -q` after the edits; it must report zero collection errors. Expected red before GREEN: AC1, AC1b, AC2, AC3, AC4, AC5 (description part), AC7, AC11. Everything else is green at RED by design (guards and retained-behavior re-points).

### GAP list: behavior that exists only in `phase_45_spec_lite` (not ported in P2b; file as follow-up for P3 or a new issue)

- **GAP-1: citation auto-correct 32ED4070.** `_repair_finding_citation` (`:670`), `_auto_correct_citations` (`:759`), `_CITATION_LINE_RE` and `_CITATION_WINDOW_LINES` (`:243-244`), called from `_write_review_doc` (`:800`). `phase_45_spec` has none of it (only `_autoprefix_bare_citations`, `:1426`, a different feature). 12 tests lost, no twin. Not trivial to port; if wanted for the single path it needs its own spec.
- **GAP-2: block-signal downgrade 39CAFF09.** `_detect_block_signals`, `_BLOCK_SIGNALS`, the `SHIP_WITH_CONCERNS` verdict. Intentionally dropped: it is the "weaker" behavior row 4 names. The infra-block case is already covered on the full path by the GH1674 shield (`E_INJECTION_MISSING`). Untested even in lite.
- **GAP-3: lite loop composite.** `build_review_loop_contract` with `consume_recoverable_retry` (`:1287-1317`). The full path uses its own retry model. Loss: `test_gh693` L2/L4/L5 and `test_bd85_retry_budgets::ac18` have no main-path counterpart; the unit twins in `test_GH681_cite_prelint_enforce.py` (`ac3`, `ac7`) are weak. Candidate for a new main-path test.
- **GAP-4: step5 telemetry cases untested on the full path.** `spec_findings_block_absent` (missing and malformed block), no-drift-when-equal, no-drift-when-unknown, cycle-2-emits-no-step5. The behavior exists at `phase_45_spec.py:4301-4335`; only the tests are missing. Also: dashboards keyed on `spec_lite_*` event names (for example `spec_lite_cycle2_abort`) go quiet; no consumer exists in `scripts/` or `commands/`.
- **GAP-5 (conditional):** e94afd31 cycle-1 reviewer cross-check directives, if the RED author finds the text only in lite's `_review_output_schema`.

## §6 Open questions: resolved (orchestrator, 2026-10-02)

1. The OSS dogfood driver lives outside this repo, so it is not in P2b scope. It is recorded in the #89 issue comment as an external consumer, to be updated when that driver next runs.
2. Yes. P2b merges before P2a, and P2a is stacked on P2b. The md flow never names lite, so every intermediate commit works.
3. GAP-1 (citation auto-correct) is recorded now in the #89 comment as a candidate for P3 or a separate issue. P2b does not port it.
4. GAP-3 and GAP-4 are test coverage on kept, shared code, so RED must port them, not drop them. Where `phase_45_spec` already has the behavior (step5 telemetry `:4301-4335`, retry budgets), RED adds main-path equivalents of the retired lite cases in the RED file. If a case has no main-path behavior at all (the lite-only loop composite), it is retired and listed in the RED file header as GAP-3.
