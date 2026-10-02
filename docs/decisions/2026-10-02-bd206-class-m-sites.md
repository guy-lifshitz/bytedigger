# bd#206: class-M blocks declared at the carry sites

**Status:** r2, post gate r1 (REJECTED: 3 MAJOR + 8 MINOR + 4 NIT, `2026-10-02-bd206-gate-r1.md`; all folded, §8) · **Tier:** 3 (two phase-6 carry chains, the semantic verifier, the checker, one new lib
module; Option D) · **Class:** SYSTEMATIC · **Chokepoint:** declaration goes through the existing channel only:
builders record `{source_id, content}` in `data["injected_blocks"]` (`_injected_blocks_record`) and the dispatch passes
`_declared_injections(...)`; `_dispatch_backend` verifies and attests as today (AUTHORSHIP_SPEC §4 Binding). The review
invocation's id reaches later steps through ONE writer/reader pair, `lib/findings_provenance.py`. No new emitter, no
change to `_dispatch_backend`. **Side of the seam:** engine. **Source:** bd#206; AUTHORSHIP_SPEC §4 class M matching
rule (fixed by #152).

## §1 Problem (measured on `66697d2`)

1. `_dispatch_backend` stamps `result.data["invocation_id"]` (#152) but no producer keeps it. Three class-M carries
   reach a later prompt undeclared, covered by R3.1's hash only:
   - **M1 satisfaction → fix.** `invoke_satisfaction_llm` (single: `phase_6_review.py:3203`; COMPLEX: n=3 via
     `_run_satisfaction_evaluators_parallel`, merged at `:3225-3236`, where each evaluator's `invocation_id` is dropped).
     Fixes (`structured.fixes_required`, or `_aggregate_satisfaction`'s concatenation over `valid` evaluators at `:3152`) are rendered
     by `_render_satisfaction_findings` (`:3569`), forwarded by `_satisfaction_fix_loop` (`:3589`) as
     `forwarded["findings"]`, survive the retry (`engine.py:700`, `:1172`, non-control keys forwarded) and are inlined
     by `_build_fix_prompt` (`:2367`). Dispatched by `_invoke_fix_llm` (`:2508`) and `invoke_fix_llm_retry` (`:2619`)
     with `_declared_injections(prev.data)`, whose record (`:2450`) holds only the in-scope test files.
   - **M2 review doc → `last_findings.json` → next review prompt.** `_persist_satisfaction_last_findings` (`:3635`)
     parses the review doc (written by the `invoke_review_llm` invocation) with `extract_structured_findings` and
     writes `{attempt, score, threshold, review_doc_path, structured_findings}`. `_build_review_prompt` (`:933-960`)
     inlines `json.dumps(structured_findings, indent=2)` (re-rendered). The review invocation's id is not reachable at
     either end: ~12 steps rebuild their data between `invoke_review_llm` and `write_satisfaction_doc`.
   - **M3 review doc → semantic verifier prompt.** `_invoke_verifier_agent` (`semantic_verifier.py:119`) inlines the
     finding's `file`, `line`, `quote`, `claim` (parsed from the review doc, `_make_finding` `:240`) and dispatches with
     no `injections` (`:180`).
2. `conformance/bd_l3.py::_r32` (`:125`) checks only that each block has a non-blank `source_id` and `sha256`. Nothing
   checks the `invocation:` form, that it names an earlier attestation in the log, or the form of `output_sha256` /
   `invocation_id`.

## §2 Decision

1. **Block shape (all sites).** `{"source_id": "invocation:<step_name>:<invocation_id>", "content": <string as
   inlined>}`. `<invocation_id>` matches `^[0-9a-f]{32}$`. A site declares nothing when it has no valid id (no run
   context / no event log / legacy record). Empty content is not declared. All content is re-rendered, so none is
   expected to equal the named attestation's `output_sha256`; the check is existence (§2.6).
   **`run_id` everywhere** is `telemetry_ctx.get_current_run().run_id` (as `phase_6_review.py:4080`), never
   `ctx.session_id`. With no current run nothing is written, the sidecar `run_id` is `null`, and nothing is declared.
   **Class-M chunk rule:** one block per carried unit (M1: one evaluator's rendered lines; M2: the inlined prior-findings
   JSON; M3: one finding field), content = the string exactly as inlined. Engine markers inside a unit (M1 `"- "`,
   `": "`; M2 JSON syntax) stay inside the block: declared limit (§5).
2. **M1.** Each `evaluator_responses` entry gains `"invocation_id"` (the evaluator result's `data["invocation_id"]`, or
   `None`). The findings are rendered per contributing evaluator, in the order `_aggregate_satisfaction` concatenates
   them (`valid` in index order; single path: one group, id = `prev.data.get("invocation_id")`). The n_valid==1 survivor
   never reaches the fix loop (`E_REVIEW_DEGRADED` at `:3934-3944`, `:4013`); GREEN adds no code for it. One block per group with
   non-empty fixes: content = that group's lines joined by `"\n"`; the full findings string stays byte-identical to today
   (`"\n".join` of the groups). `_satisfaction_fix_loop` forwards `forwarded["findings_blocks"]` (list of block dicts);
   absent when no group has an id. The fallback text (`error_msg`, class E) is never declared.
   `_build_fix_prompt` adds `prev.data["findings_blocks"]` to `_declared_blocks` only when `sat_loop` is true and the
   inlined findings string equals `"\n".join(b["content"] for b in findings_blocks)`; otherwise none (stale/forged guard).
   Order: findings blocks are declared in prompt order relative to the test-file records.
3. **Review provenance (M2, M3).** New `lib/findings_provenance.py`:
   - `write_review_provenance(review_doc_path, run_id, invocation_id)` writes
     `<dir of review_doc_path>/review_invocation.json` = `{"step_name": "invoke_review_llm", "invocation_id", "run_id",
     "review_doc_path"}` via `atomic_write`. `_invoke_review_llm` calls it after dispatch when the result carries a valid
     `invocation_id`, `result.status == "ok"` and a current run exist; before dispatch it unlinks the file (beside its
     existing `role-*.md` cleanup, `:1053-1061`). An error result, including a pin/escape refusal that carries an id,
     writes nothing, so a failed or unattested review leaves no file. A sentinel-resumed review does not run the step; the file from the
     original dispatch stands.
   - `review_source_id(review_doc_path, run_id) -> str | None` reads that file and returns
     `"invocation:invoke_review_llm:<id>"` only when the file parses, `run_id` and `review_doc_path` match the caller's
     and the id is valid; every failure returns `None` and never raises. This is the module's only read.
4. **M2.** `_persist_satisfaction_last_findings` adds `"run_id"` and `"source_id"` (from `review_source_id`, may be
   `null`) to the sidecar. `_build_review_prompt` declares one block, content = the inlined `_prior_findings_json`,
   source_id = the sidecar's `source_id`, only when it is a valid `invocation:` id, the sidecar's `run_id` equals
   the current run's, **and** `structured_findings` is a non-empty list (`"[]"` carries no model bytes). Position in
   `_declared_blocks`: after the post-fix report (`:824`), before F1 (`:976`) — prompt order. A sidecar from another run, or without the keys (legacy), declares nothing.
5. **M3.** `verify_findings_semantic` computes `review_source_id(review_doc_path, run_id)` once and sets it on each
   finding dict as `finding["source_id"]` (an engine key; `_invoke_verifier_agent`'s signature is unchanged).
   `_invoke_verifier_agent` declares one block per non-empty `str(finding[k])` for k in `file`, `line`, `quote`, `claim`
   (a caller may pass `"line": 1`), in prompt order,
   all with that source_id, and passes `injections=` **only when** it declares at least one block (existing fakes
   without the kwarg keep working when there is no provenance). Both the haiku call and the opus escalation declare.
6. **Checker (`bd_l3._r32` + payload form).** `check_bd_l3` keeps, in log order, the set of `(event["run_id"],
   step_name, invocation_id)` of attestations already seen. For a block whose `source_id` starts with `"invocation:"`:
   it must match `^invocation:([^:]+):([0-9a-f]{32})$` and name a triple with the **current event's `run_id`** from a
   **strictly earlier** event, else an `R3.2:` violation. Order: check the payload's blocks first, then add the
   payload's own triple (self-reference fails). A cross-run reference fails even when the CLI runs without `--run-id`. A payload whose `output_sha256` is present and not `None` must be `sha256:` + 64 lowercase hex, and
   `invocation_id`, when present, must match `^[0-9a-f]{32}$`; else an `R3.2:` violation, and the payload counts as
   R3.2-observed. `output_sha256: null` is not a violation (real `data=None` results, `llm_subprocess.py:1311-1314`).
   Payloads without those keys (pre-#152 logs) are not violations. These form checks sit under R3.2, not R3.1, because
   both fields exist to resolve class-M `source_id`s; R3.1 stays the prompt-hash requirement.
7. **AUTHORSHIP_SPEC.** §4 class M: declared at M1/M2/M3 (bd#206); the "deferred to bd#206" sentence is replaced; the
   still-undeclared class-M carries are listed (§5); the class-M chunk rule of §2.1 is added; the literal
   `invocation:<step_name>:<invocation_id>` stays (bd152 AC10). §2.2 R3.2 row: the phrase "class-M block declarations are a follow-up (bd#206)" becomes "class-M blocks are
   declared at the three carry sites (bd#206)". `class_i_inventory.json`: one new key
   `lib/findings_provenance.py::review_source_id::read_text#0`, class `not-prompt`. Existing class-M keys keep their
   class; their notes name bd#206 where a declaration now exists.

## §3 Acceptance criteria

Fixtures: real `telemetry_ctx` run with a real `EventLog` on `tmp_path`; a recording backend via `register_backend`
returning a fixed `raw_response`; assertions read `model_invocation_attested` events back from the log (§1l), never the
builder's in-memory record alone.

- **AC1 (M1 single, end-to-end).** Satisfaction FAIL (fixable reason) with 2 fixes → retry → `invoke_fix_llm` event's
  `injections` contains exactly one block with `source_id == "invocation:invoke_satisfaction_llm:" + <id of the
  satisfaction event in the same log>` and `sha256` = sha256 of the two rendered lines joined by `"\n"`, computed in the
  test. The fix prompt equals the prompt `_build_fix_prompt` returns for the same fixture with no event log (no
  `findings_blocks`). Same fixture with a first fix response lacking the marker → the `invoke_fix_llm_retry`
  attestation carries the same class-M block.
- **AC2 (M1 COMPLEX).** Three evaluators, two with fixes (A: 2, B: 0, C: 1) → two blocks, in A-then-C order, each
  naming its own evaluator's logged id; each digest covers only that evaluator's lines. `evaluator_responses[i]
  ["invocation_id"]` equals the i-th evaluator's logged id.
- **AC3 (M1 guards).** (a) No fixes (fallback text) → no class-M block. (b) `findings_blocks` whose joined content ≠ the
  inlined findings → no class-M block and the fix dispatch is not refused. (c) No event log → no `findings_blocks` key.
  (d) Decoy: review-driven fix prompt (`fix_loop_source` absent) whose `prev.data` carries `findings_blocks` and a
  matching `findings` → no class-M block, dispatch not refused with `E_INJECT_UNATTRIBUTED`.
- **AC4 (provenance).** `_invoke_review_llm` with event log → file exists with the logged review event's id and the
  telemetry run id, with `ctx.session_id` set to a different value; a review dispatch returning error (no id), and an
  error result **with** a stamped id (`E_MODEL_PIN_MISMATCH`) → file absent, including when a stale one existed before.
  `review_source_id` returns `None` for: missing file, bad JSON, other `run_id`, other `review_doc_path`, id not 32-hex;
  never raises.
- **AC5 (M2).** Sidecar written on satisfaction FAIL carries `run_id` and `source_id` = the review event's id form. Next
  `_build_review_prompt` + `_invoke_review_llm` in the same run → the review event's `injections` contains a block with
  that `source_id` and the digest of the inlined `json.dumps(structured_findings, indent=2)`. Same sidecar with a
  different `run_id`, a legacy sidecar without the keys, and a same-run sidecar with `structured_findings: []` → no
  class-M block; prompt bytes identical across the first three.
- **AC6 (M3).** With provenance present, each `verify_findings_semantic` attestation (haiku and opus escalation)
  carries blocks for the non-empty fields of its finding, in `file, line, quote, claim` order, source_id = the review
  id; fixture sets `ctx.session_id` ≠ the telemetry run id and a finding with `"line": 1` (content `"1"`). Without
  provenance: no `injections` kwarg is passed to `invoke_llm_subprocess` (recording fake asserts kwargs).
- **AC7 (checker, one log per case).** `failed` cases: block naming an attestation that appears **later**;
  self-reference (names its own payload's id); malformed `invocation:x:short`; right id, wrong step; run B naming run
  A's attestation with A and B interleaved in one log; `output_sha256: "deadbeef"`; `invocation_id: "XYZ"`. `passed`
  cases, asserted as R3.2 `passed` **and** `violations == ()`: the same block placed after the attestation it names (same
  run); `{"invocation_id": <valid>, "output_sha256": null}`; a pre-#152 payload without both keys. A real log from AC1 →
  R3.2 `passed`.
- **AC8 (docs + lint).** AUTHORSHIP_SPEC §4 class-M paragraph (sliced `**Class M,` to `**Chunk rule.**`) no longer
  contains "deferred to bd#206", contains `invoke_satisfaction_llm`, `last_findings.json`, `verify_findings_semantic`
  and still `invocation:<step_name>:<invocation_id>`; the R3.2 `REQUIREMENT_LABELS` row no longer contains "class-M
  block declarations are a follow-up (bd#206)" and contains "declared at the three carry sites (bd#206)". `class_i_lint.check` on the tree returns `[]` and the inventory has
  the §2.7 key with class `not-prompt`.

## §4 Siblings (§1a) — scoped before and after, delta on base `66697d2`

Scoped: `test_bd10_l3_authorship.py`, `test_bd28_bd_l3_checker.py`, `test_bd141_p4_bd_l3_cli.py`,
`test_bd73_r31_r32_verdicts.py`, `test_bd155_l2_event_type_key.py`, `test_bd147_injected_segments.py`,
`test_bd150_class_i_inventory.py`, `test_bd141_p4d_role_template_injections.py`, `test_bd82_semantic_verifier_chokepoint.py`,
`test_semantic_verifier_W15.py`, `test_semantic_verifier_F60FED11.py`, `test_3C533CD8_semantic_verifier_model_pin.py`,
`test_phase_6_satisfaction_multi_evaluator.py`, `test_phase_6_last_findings_persist_c834481a.py`,
`test_phase_6_subagent_prior_context_propagation_7ca211d2.py`, `test_bd85_retry_budgets.py`,
`test_phase_6_fix_inline_head_tests_65EA1B86.py`, `test_gh705_callsite_stable_prefix.py`,
`test_BC45C403_phase6_step_sentinel_resume.py`, `test_bd139_single_reviewer.py`, `test_bd152_output_digest.py`,
`test_phase_6_satisfaction_gate_strict_and_9702b73f.py`, `test_phase_6_degrade_fail_loud_F81D5EF7.py`,
`test_gh925_terminal_fail_sentinel_invalidation.py`, `test_gh751_satisfaction_spec_anchor.py`,
`test_gh432_ac_checklist_parser_contract.py`, `test_gh388_ac_checklist_satisfaction.py`,
`test_subagent_return_discipline_satisfaction_1A07C325.py`, `test_phase_6_satisfaction_structured_verdict.py`,
`test_bd89_p3b1_single_reviewer_only.py`, `test_bd82_role_backend_effort.py`, `test_gh499_oss_string_residue.py`,
`test_engine_retry_data_forwarding.py`, `test_bd119_role_template.py`.
Base run (`66697d2`, `cd engine_py/tests && python3 -m pytest -q` on the list): **781 passed, 1 skipped** (34 files, r2 list).
Expected over-constraints to amend in RED (and only those): exact-shape asserts on `evaluator_responses` entries or on
the sidecar's key set; listed per site in RED's report.

## §5 Declared limits / still undeclared

- Attribution is per carried section (M2) or per field (M3), not per finding line; the merged M1 string is attributed
  per evaluator.
- Re-rendered content never equals `output_sha256`; the checker proves the named invocation was attested earlier in
  the run, not that the content came from its answer.
- Review doc content changed after the review invocation (role-file aggregation, verifier `[UNVERIFIED]` markers,
  refuted section) is attributed to the review invocation only.
- A dropped attestation (`_emit_safe` write failure) turns a later legitimate class-M block naming it into R3.2
  `failed` (fails closed).
- Cross-run sidecars (orchestrator re-invokes phase 6 under a new `run_id`) declare nothing (§2.4).
- Not declared (re-open: next lot): phase_45 delta-prompt findings and `.findings-thread.json`
  (`phase_45_spec.py:1034-1060`); phase_5 GREEN delta `findings` (`phase_5_implement.py:1288`); verifier verdict text
  reaching later prompts through the review doc; other workflows calling the semantic verifier (no provenance file).

## §6 Not in scope (§1v)

`llm_subprocess.py` (no change), `engine.py`, `phases/*.md`, `phase_45_spec.py`, `phase_5_implement.py`,
`findings_sidecar.py`, `lib/reference_backends/*`, `class_i_lint.py`. bd#192 (class-I tails) is the next lot.

## §7 Scope (GREEN)

`workflows/phase_6_review.py` (`_invoke_satisfaction_llm` merge, `_aggregate`/render grouping, `_write_satisfaction_doc`
FAIL paths, `_satisfaction_fix_loop`, `_build_fix_prompt`, `_invoke_review_llm`, `_persist_satisfaction_last_findings`,
`_build_review_prompt`), `lib/plugins/anti_hallucination/semantic_verifier.py`, `lib/findings_provenance.py` (new),
`conformance/bd_l3.py`, `conformance/AUTHORSHIP_SPEC.md`, `conformance/class_i_inventory.json`, `CHANGELOG.md`.
RED: `engine_py/tests/test_bd206_class_m_sites.py` (new) + §4 amendments.

## §8 r2 changes (gate r1)

M-1 run-scoped checker → §2.6, AC7. M-2 one log per case, self-reference, `output_sha256: null`, check-then-add → §2.6,
AC7. M-3 `run_id` pinned to the telemetry run → §2.1, AC4, AC6. m-1 survivor clause removed → §2.2. m-2 write only on
`status == "ok"`, factual fix → §2.3, AC4. m-3 R3.2 row assertion → §2.7, AC8. m-4 empty prior findings → §2.4, AC5.
m-5 class-M chunk rule + `str(value)` → §2.1, §2.5, §2.7. m-6 `invoke_fix_llm_retry` → AC1. m-7 siblings → §4,
re-measured. m-8 in-tree comparison → AC1. N-1 rename. N-2 position → §2.4. N-3 reason kept under R3.2 → §2.6. N-4 → §5.
Decoy `sat_loop` edge → AC3(d).
