# Gate r1: bd#141 item 6 review-readiness gate (spec + RED)

- Spec: `docs/decisions/2026-10-02-bd141-review-gate.md` (r1 DRAFT)
- RED: `engine_py/tests/test_bd141_p6_review_gate.py` (reported: 32 failed, 1 passed, R13)
- Precedent: `2026-10-02-bd141-start-gate.md`, `phase_5_implement._readiness_start_gate` (phase_5_implement.py:7876)
- Tier 2, round 1 of 3. This was a read-only audit. I ran no tests and no git commands.

## Step 1: spec-internal consistency

These literal tokens are the same everywhere they appear in the spec and the RED file: `_readiness_review_gate`, `readiness_review_verdict`, `E_READINESS_NOT_APPROVED`, `review_label`, `build_review_prompt` (step_name), `_readiness` alias, `ambient_git_cwd` and `start|review|ship`. The five event keys in op2 match `EVENT_KEYS` in the RED file. The error format in op2 matches R15 exactly. I found no drift.

One wording drift sits outside the spec. `scripts/readiness:3` has the usage comment `--stage {start|ship}`, and §1v puts that file out of scope (finding m4).

## Step 1.5: rule-overlap simulation (`_decide`, readiness.py:491-528, with `pol._replace(label=review_label)`, stage `review`)

Branch order: no record → invalid record → ship-retry (`stage == "ship"`) → approval_consumed (`owner[1] != branch`) → label_absent → label_predates_spec → approver → self → spec_changed (`stage == "start"`) → APPROVED.

| AC | rig | first matching branch | expected | ok |
|---|---|---|---|---|
| R3 | record@200, ready ev@400, label on | APPROVED (owner None; stage != ship/start) | APPROVED | yes |
| R4 | no ready event or label | label_absent | label_absent | yes |
| R5 | record@500, ready ev@400 | label_predates_spec | same | yes |
| R6 | distinct_actor, ready by bd-bot | self_approved | same | yes |
| R7 | approvers=[alice], ready by bob | approver_not_allowed | same | yes |
| R10b | start, ready label only | label_absent (want=plan label) | same | yes |

The claims hold:
- `approval_consumed` cannot fire. `_consumption_owners` is keyed by event id, and `_consume` (readiness.py:537) only ever writes the id of the plan-label event.
- The ship-retry branch (readiness.py:514) and `spec_changed` (readiness.py:526) are both guarded by stage.
- `_evaluate` reads the spec only at `start` (readiness.py:607) and consumes only at `ship` (readiness.py:611).

Two things GREEN has to do that the current code does not:
- `_bind` (readiness.py:590-591) sets `required=True` and `label=pol.label` before it reaches any review-specific logic. At `review`, GREEN must check `review_label is None` first, so the result is exactly `_default_result()` (R1). It must also put `review_label` into `label` (R3).
- R9 adds a type check on `review_label` inside `_load_policy`. That makes `ship` exit 4 (fail-closed) on a bad `review_label`. op1 declares this ("every stage becomes UNAVAILABLE"). It matches how the other policy fields fail today, and since `review_label` is a new key no existing repo can regress. I find it acceptable, but see m2 about the ordering and §4.

## Step 2: §2 against §3

- op1 → R1–R12. op2 → R14–R19. op3 → R13, R20, R21. op4 → R22, R23. Each op has ACs.
- **Mismatch (M1).** The refusal is a terminal phase error. In production it is not handled only by the helper. `phase_6_review_workflow` declares `error_handler=_on_phase_6_abort` (phase_6_review.py:5924). The engine calls that handler for every terminal `error` (engine.py:373 and 456-460), and it writes `reviews/build-satisfaction.md` as a NOT_ASSESSED stub (phase_6_review.py:5880-5915). Because `recoverable=False`, the engine also emits the dispatcher report and a `terminal_failure` stuck-report (engine.py:407-430).
  - §2 declares none of this, and no AC covers it.
  - R20 says "no file exists under `<scratch>/reviews/`". That is true only because R20 calls the step directly. On the production terminal path, `reviews/` does gain a file. So the §3 post-condition has no producing path in production.

## Step 3: RED adequacy

- Coverage: R1–R23 each map to at least one `test_r<N>_*`. R9 is parametrised over 3×3, R17 has a/b/c, and R13 is a structure pin that is green before GREEN by design.
- Collection: the new symbols are reached only inside test bodies, through `_gate()`, `_rd()` and `getattr`. Collection cannot error, which agrees with the observed 32F/1P.
- Where each test fails before GREEN:
  - R9 (start/ship), R11, R12, R14–R23 fail at an assert. R14–R19 fail on `_gate()`/`_rd()` asserts, R12 on rc 2 ≠ 3, R11 on the message.
  - R1–R8, R10 and R9[review] fail with a ValueError raised by `readiness._verdict` (readiness.py:633), not at an assert. The reason is right (stage not supported), and none of this happens at collection, so the §1q hard bar is met. But the module docstring's claim "each RED fails at an assert" is not true (m5).
- Can each negative test go red? Yes. For every negative AC there is a concrete code change that turns it red:
  - R4: `review` reusing `pol.label`.
  - R10: `start` reading `review_label`.
  - R14: gating without the key; the rig then has no labels, so it would refuse and emit an event.
  - R16: consuming.
  - R18: wrong stage.
  - R19: no ambient guard; the spy raises, the helper swallows it, and `calls == []` goes red.
  - R20: the gate placed after `_review_plan`.
  - R21: the gate always refusing.
  - Exception: R20's `reviews/` emptiness assertion cannot go red, because the recorder raises before anything is written whether or not the gate exists. `calls == []` is what carries R20 (part of M1).
- Over-specification:
  - R18 pins exactly `(repo, "review")` positionally, with no kwargs. That is what op2 says literally, so it is consistent, not beyond the spec.
  - R21's second refusing run repeats R20. That is harmless, and it is the half that makes R21 red before GREEN; the OFF half passes on the current code. Acceptable.
- §1l: no test mocks its own unit under test.
  - The spies replace `verdict` and `resolve_git_cwd_with_source`, which are collaborators of the helper.
  - R20/R21 run the real `_build_review_prompt` with the real `readiness.verdict` against a real git remote and a fake gh, and replace only the downstream `_review_plan`.
  - R3–R12 run the real `readiness`.

## Step 4: reachability (§1y)

| AC | Point | Host | Test path |
|---|---|---|---|
| R20/R21 | new `refusal = _readiness_review_gate(...)` as the first statement | `phase_6_review._build_review_prompt` (phase_6_review.py:733) | `StepContract("build_review_prompt")` (phase_6_review.py:5926) → engine `_execute_steps` (engine.py:504) |
| R3–R12 | `_verdict` stage set (readiness.py:633), `_bind`/`_decide` | `readiness.verdict` | `_readiness_review_gate` → `verdict(repo,"review")` |
| R16 no-consume | `_evaluate` guard (readiness.py:611) | `readiness._evaluate` | R16 fake-gh log |

There is no other production caller of `phase_6_review._build_review_prompt`. Repo-wide grep finds only the StepContract at 5926; the phase 4.5 functions with the same name are separate. `phase_6_review_workflow` is registered once (workflows/__init__.py:42).

## Answers to the brief

1. **Placement and bypass.** Placing the gate as the first statement of `p6._build_review_prompt` is correct, and nothing bypasses it.
   - The engine always runs Phase 6 from step 0. `start_step` > 0 happens only for an in-phase `retry_from_step`.
   - The resume sentinel caches **step 2 only** (`step_sentinel.maybe_read_sentinel`, step_sentinel.py:146; it is per step). So the §4 limit "resumes that skip step 1 skip the gate" is wrong: the gate runs on every resume. The real behaviour is stricter: a resume whose review output is already cached still refuses if the ready label has since been removed (m1).
   - The only path that does not re-run step 1 is the satisfaction fix loop (`retry_from_step_idx=target`, the fix step, phase_6_review.py:3699). It runs after a gate that already passed, so it is harmless, but it is not declared.
2. **readiness.py.** R3–R8 behave as claimed (Step 1.5). The R9 fail-closed effect on `ship` is declared in op1 and acceptable. §4 should say it in plain words, and the ordering against `required` should be pinned (m2).
3. **RED quality.** All negative tests can go red, except the R20 `reviews/` assertion (M1). Nothing mocks its own unit under test. R18 and R21 do not go beyond the spec. §1q is met at the hard bar, but 9 tests fail through a production ValueError rather than an assert (m5).
4. **Sibling tests (§1a).** None of the sibling tests that call `p6._build_review_prompt` or run `phase_6_review_workflow()` points `git_cwd` at a repo that has an `origin`.
   - I grepped the 38 test files that mention `_build_review_prompt|phase_6_review_workflow()` for `remote add` / `clone` / a `git_cwd` or `scratchpad_dir` derived from `REPO_ROOT`/`__file__` and found 0 hits.
   - The repos with an explicit `git_cwd` (test_gh268, test_F9F7E4FD, test_bd141_p4d, test_gh1591, test_gh1626b, test_phase_6_commit_fix_tests_8FE3D757, …_post_fix_typecheck_gate_GH316, …_post_fix_pytest_gate_7A940850, …_step10_commit_fix_code) are created with `git init` only, with no remote. So `read_policy_blob` returns None, the gate is OFF, and no event is emitted.
   - The ambient siblings get one extra `readiness_review_verdict` event. The event spies in phase-6 siblings either filter by event type (test_phase_6_test_only_note_FF2CB91D:315, test_906e37dc:359ff) or are no-ops. They all take 2 positional args, which the op2 call form already satisfies.
   - The real risk is zero. The "about 20 files" count in spec §5 is low, though (m3).
5. **Factual spot-checks.**
   - Correct against the current tree: readiness.py:633 (stage check), :283 (label default), phase_6_review.py:5921 (workflow), :5249 (the `resolve_git_cwd_with_source(cfg)` pattern), :733 (`_build_review_prompt`), phase_45_spec.py:3982, phase_45_spec_lite.py:543.
   - Zero `readiness` hits in phase_6_review.py: confirmed on this worktree.
   - The R13 pinned list matches phase_6_review.py:5926-5962 exactly (21 steps).
   - I could not check `5d15316` as a commit (no git allowed). The cited content matches the worktree.

## Adversarial edges (not covered by any §3 AC)

1. **OFF plus no issue.** `required: true`, no `review_label`, branch `feature-x`: the spec says OFF (`_default_result()`). A GREEN that keeps the `_bind` order would return `NOT_APPROVED no_issue` and block Phase 6. R1 uses `gh42-x` and R8 has the key set, so neither catches this.
2. **`required: false` plus a malformed `review_label`.** The spec does not say whether the type check runs before `if not required` (readiness.py:292). A GREEN could go either way, and R9 only covers `required: true`.
3. **Plan approval consumed by another branch, valid ready label present.** `consumption(…, "LE_1", "other-branch")` together with a ready event: review must be APPROVED. This guards the spec's own claim "approval_consumed cannot occur" against an implementation that looks up the owner using the plan event.
4. **Resume with a cached `invoke_review_llm` sentinel and the ready label removed.** The gate refuses, contradicting §4 (m1).
5. **The bd#85 driver treats `E_READINESS_NOT_APPROVED` as a plain error.** The code is not in `_STOP_CODES` (lib/task_resume.py:60), so `plan_resume` auto-resumes Phase 6. Each re-run refuses again and uses up the task run cap (`DEFAULT_MAX_RUNS = 3`) before any human acts. This is inherited from the start gate, but §4's "resumes after a human adds the label" does not mention it (m6).

## Findings

**M1 — MAJOR (§2 against §3, terminal path).** spec op3 (lines 90-97), §4 (line 214), and R20 (spec line 171; test_bd141_p6_review_gate.py:480-481).
- The refusal is a terminal `error` from a workflow that declares `error_handler=_on_phase_6_abort` (phase_6_review.py:5924). The engine (engine.py:373, 407-430, 456-460) therefore writes `reviews/build-satisfaction.md` (a NOT_ASSESSED stub naming `build_review_prompt` / `E_READINESS_NOT_APPROVED`) and emits a dispatcher report and a `terminal_failure` stuck-report.
- The spec does not declare any of this. R20's "no file exists under `<scratch>/reviews/`" is false on the production path, and its assertion cannot go red in the test.
- **Fix:**
  - (a) In op3 and §4, declare that a refusal goes through the GH1626 B abort handler: the stub is written, a later full Phase 6 run replaces it, and the stuck/dispatcher report is emitted.
  - (b) Reword R20 to "no review prompt or role file under `reviews/`" (for example, exclude `build-satisfaction.md`, or assert that `_review_plan` was not called and that `reviews/role-*.md` and the review doc are absent). Drop the emptiness assertion that cannot go red.
  - (c) Add an AC, R24: run `p6._on_phase_6_abort(refusal, ctx)`, or `phase_6_review_workflow()` through the engine on the R4 rig. The stub must contain `E_READINESS_NOT_APPROVED` and `build_review_prompt`, and the step-2 spy must see zero calls.

**m1 — MINOR (factual).** Spec §4 line 209: "Phase 6 resumes that skip step 1 (`invoke_review_llm` resume sentinel) skip the gate" is wrong. Sentinels are per step (step_sentinel.py:139-163), and the engine re-runs step 0 on every execute.
- **Fix:** replace the line with: the gate runs on every Phase 6 entry, including resumes. A resume whose review is already cached still refuses if the ready label was removed or a newer record was posted. Only the in-phase satisfaction fix loop (phase_6_review.py:3699) re-enters after step 1, and it does so after a gate that already passed.

**m2 — MINOR (op1 ambiguity).** Spec lines 33-37 do not say where the `review_label` type check sits relative to `if not required: return None` (readiness.py:292).
- **Fix:** state that it runs with the other field checks, before `required`. Add an R9 row with `required: false` plus a bad key → UNAVAILABLE. In §4, add: "a malformed `review_label` makes `check --stage ship` exit 4 even if review is never used."

**m3 — MINOR (§1a list).** Spec line 234 says "about 20 files". The broader grep finds 38 test files that mention `_build_review_prompt|phase_6_review_workflow()`.
- **Fix:** paste the exact output of the §5 grep pipeline as the sibling list, for `--require-clean`.

**m4 — MINOR (drift).** `scripts/readiness:3` still says `--stage {start|ship}`, and §1v (spec line 226) puts the file out of scope.
- **Fix:** bring that comment line into scope, or declare the stale usage comment in §4.

**m5 — MINOR (§1q hygiene).** test_bd141_p6_review_gate.py:8-10 claims every RED fails at an assert. R1–R8, R10 and R9[review] instead fail through a `ValueError` from readiness.py:633.
- **Fix:** make `_verdict()` (test line 101) catch `ValueError` and call `pytest.fail(f"stage 'review' not supported: {e}")`, or correct the docstring.

**m6 — MINOR (declared limits).** Two items:
- The `E_READINESS_NOT_APPROVED` description (error_codes.py:170) says "no current `plan-approved` approval". With this lot that is wrong for review refusals. op4 only appends `phase_6_review`.
- §4 does not mention the bd#85 driver interaction (adversarial edge 5).
- **Fix:** reword the description to cover "or no current review label", and add a §4 bullet saying the driver auto-resumes a refused Phase 6 up to the run cap (a follow-up issue for adding `E_READINESS_NOT_APPROVED` to `_STOP_CODES`, which affects the start gate too).

**Advisory (no finding):** edges 1-3 above are cheap to add as R1b / R9b / R3b and would turn three spec claims into guards that can go red.

VERDICT: REJECTED
