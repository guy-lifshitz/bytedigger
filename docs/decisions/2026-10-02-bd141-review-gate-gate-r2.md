# Gate r2: bd#141 item 6 review-readiness gate (spec r2 + RED r2)

- Spec: `docs/decisions/2026-10-02-bd141-review-gate.md` (r2)
- RED: `engine_py/tests/test_bd141_p6_review_gate.py` (reported: 34 failed, 1 passed, R13)
- r1 report: `2026-10-02-bd141-review-gate-gate-r1.md` (M1 + m1–m6)
- Tier 2, round 2 of 3. Read-only audit; no tests, no git run. The whole surface was re-audited, not only the cited lines.

## r1 closure

| r1 | status | evidence |
|---|---|---|
| M1 | closed | op3 "Terminal path" declares the GH1626 B stub plus the dispatcher and stuck reports (matches engine.py:373, 407-430, 456-460). R20 now asserts recorder calls == [] and `error_code`. The emptiness assertion is gone. R24 added (test :511-523). |
| M1, R24 stub check | holds | `_render_not_assessed_stub` (phase_6_review.py:5858-5877) writes `Aborting step: {result.step_name}` and `Error code: {result.error_code}`. With the op2 StepResult (`step_name="build_review_prompt"`, `error_code="E_READINESS_NOT_APPROVED"`), both tokens are in the body. `_on_phase_6_abort` (:5880) writes to `<scratchpad_dir>/reviews/build-satisfaction.md` (SATISFACTION_DOC_RELPATH :265). The doc is absent in the rig, so the whitelist allows the write. Before GREEN, R24 fails at `assert res is not None`, because the recorder raises `_Stop` and `_run_step` returns None. That is an assert-time failure. R24 duplicates R15's code and step-name checks. Its new value is that the handler actually writes the stub, which is acceptable. |
| m1 | closed | §4 bullet 3 is correct (per-step sentinels; step 0 re-runs on every execute; the fix loop at :3699). |
| m2 | closed | op1 places the check before `if not required` (readiness.py:292). The R9 `required:false` row exists (test :228). The §4 ship-exit-4 bullet is present. |
| m3 | closed | 39 files listed. A grep of `_build_review_prompt\|phase_6_review_workflow` gives 40 files. The listed set is that 40, minus the new RED file, the three phase-4.5 files and gh605 (which do not mention `phase_6_review`), plus the readiness siblings (bd117a, bd141_p5, bd141_p6_start_gate). It is consistent. |
| m4 | closed | `scripts/readiness` is in §5 (line 3 only), and R23 asserts `{start\|review\|ship}`. |
| m5 | **closed, but the fix introduced F1** | `_verdict()` now turns ValueError into `pytest.fail`. See F1. |
| m6 | closed | op4 rewords the description ("or no current review label"), and R23 asserts `review label`. The §4 bd#85 `_STOP_CODES` bullet is present (task_resume.py:55, 60 confirmed). |

## Step 1: spec-internal consistency

These tokens are stable across the spec and the RED file: `_readiness_review_gate`, `readiness_review_verdict`, `E_READINESS_NOT_APPROVED`, `review_label`, `build_review_prompt`, `_readiness`, `ambient_git_cwd`, `start|review|ship`, `{start|review|ship}` and the five event keys.

One cosmetic drift: spec line 58 says "Choices become `start`, `review`, `ship`." twice, once in the bullet text and once repeated after the m4 parenthetical (F3).

## Step 1.5: rule-overlap simulation

The `_decide` walk at stage `review` (readiness.py:491-528, `pol._replace(label=review_label)`) is the same as in r1. R3 → APPROVED, R4 → label_absent, R5 → label_predates_spec, R6 → self_approved, R7 → approver_not_allowed. The ship-retry branch (:514) and spec_changed (:526) are guarded by stage. `approval_consumed` cannot fire, because the owner is looked up by the review-label event id, and `_consume` (:537) only writes plan-label ids.

`_bind` (:585-599) still sets `required`/`label` before the issue binding. The spec explicitly requires GREEN to short-circuit on `review_label is None` → `_default_result()` (R1, R14).

## Step 2: §2 against §3

- op1 → R1–R12. op2 → R14–R19. op3 → R13, R20, R21. op3 terminal path → R24. op4 → R22, R23.
- The terminal/abort branch now has a producing §2 path (op3 "Terminal path") and an AC (R24).
- The dispatcher and stuck reports are declared but have no AC. They are generic engine behaviour that already exists for every `recoverable=False` error and is covered by the engine's own tests, so this is acceptable.
- No mismatch.

## Step 3: RED adequacy

- Coverage: every one of R1–R24 maps to at least one `test_r<N>_*`. 35 tests: 34 F and 1 P, which matches the report.
- Collection is safe. New symbols are touched only inside test bodies.
- Assert-time failure holds before GREEN in every test.
- §1l: nothing mocks its own UUT. The spies sit on collaborators (`verdict`, `resolve_git_cwd_with_source`, `_review_plan`). R20, R21 and R24 run the real step.
- **New: two tests can never pass after a correct GREEN (F1, F2).** Both are red for a plausible reason before GREEN, so the 34F count hides them.

## Step 4: reachability (§1y)

| AC | Point | Host | Test path |
|---|---|---|---|
| R20/R21/R24 | `refusal = _readiness_review_gate(...)` as the first statement | `phase_6_review._build_review_prompt` (:733) | `StepContract("build_review_prompt")` (:5926) → engine `_execute_steps` |
| R24 | `atomic_write(doc, _render_not_assessed_stub(result))` (:5915) | `_on_phase_6_abort` (:5880) | engine `_invoke_error_handler` (engine.py:456-460); the test calls the handler directly |
| R3–R12 | `_verdict` stage set (readiness.py:633), `_bind`, `_decide`, `_report` (:690), argparse choices (:712) | `readiness.verdict` / `main` | direct |

## Adversarial edges (not covered by any §3 AC)

1. **OFF plus no issue.** `required:true`, no `review_label`, branch `feature-x`. The spec says OFF. A GREEN that keeps the `_bind` order returns `NOT_APPROVED no_issue` and blocks every Phase 6 run in a repo with `required:true` and a non-issue branch. R1 and R14 use `gh42-x`, so neither catches it. (Carried from r1, still uncovered.)
2. **Plan approval consumed by another branch, with a valid ready label.** Review must be APPROVED. This guards the "approval_consumed cannot occur" claim against an implementation that looks up the owner by the plan event. (Carried from r1.)
3. **`readiness post` with a malformed `review_label`.** `_cmd_post` → `_bind` → `_load_policy` raises → UNAVAILABLE → `_report(stage="ship")` → exit 4. So `post` also fails closed on a bad `review_label`. §4 names only `check --stage ship` (F4).
4. **Explicit JSON `null` for `review_label`.** op1 says this is OFF. No AC covers it, so a GREEN using `"review_label" in cfg` would treat it as a type error and make every stage UNAVAILABLE.

## Findings

**F1 — MAJOR (RED cannot go green: R11). Introduced by the m5 fix.** test :258-262 calls the test helper `_verdict(rig, "deploy")` inside `pytest.raises(ValueError)`. The helper (:103-109) catches the ValueError and calls `pytest.fail(...)`. That raises `_pytest.outcomes.Failed`, a `BaseException` subclass that `pytest.raises(ValueError)` does not catch. After a correct GREEN, `readiness.verdict(repo, "deploy")` still raises ValueError (as required), so R11 fails forever. GREEN cannot fix it, because tests are read-only for GREEN (§1s).
- **Fix:** in R11, call `readiness.verdict(rig.repo, "deploy")` directly inside `pytest.raises(ValueError)`. Alternatively, make the helper call `pytest.fail` only when `stage == "review"`. Before GREEN, R11 then fails at `assert "review" in str(ei.value)` (the current message is "stage must be 'start' or 'ship'"), which is an assert-time failure.

**F2 — MAJOR (RED cannot go green: R10, rig environment hijack).**
- test :243-252 creates `rig2` *before* the final `_verdict(rig, "review")` check.
- `make_rig` (test_bd117a_readiness.py:433-441) monkeypatches the process-wide `HAL_GH_BIN`, `GIT_SSH_COMMAND` and `PATH` to rig2's fake gh, state file and bare repo. `get_config().binary` reads the environment live (config_provider.py:65-67).
- So the last line evaluates rig1's repo against **rig2's** gh state: labels `[READY]`, record ts 200, ready event LE_9 at 400 by alice.
- After GREEN, `_decide` at review then returns `APPROVED` with `reason=None`, and `assert ... == "label_absent"` fails forever.
- (R12 and R21 also create several rigs, but each check runs before the next rig is made, so they are safe.)
- **Fix:** move `assert _verdict(rig, "review")["reason"] == "label_absent"` up, directly after the first `start` check and before `rig2` is created. The general rule: never evaluate a rig after a later `make_rig` call.

**F3 — MINOR (spec drift).** Spec line 58 repeats "Choices become `start`, `review`, `ship`." Delete the duplicate sentence when the spec is next touched. GREEN does not depend on it.

**F4 — MINOR (undeclared effect).** A malformed `review_label` also makes `readiness post` exit 4 (edge 3). Add "and `readiness post`" to the §4 m2 bullet. No code change.

**F5 — MINOR (advisory, carried).** Edges 1, 2 and 4 are cheap guards that can go red: R1b (`feature-x`, no `review_label` → `_default_result()`), R3b (`consumption(LE_1, other-branch)` plus ready label → APPROVED), and R9b (`review_label: null` → OFF). Edge 1 is the most valuable, because the current `_bind` order makes that wrong GREEN the natural one. Add them in RED r3, since a test change is needed anyway.

The MAJORs are test-file defects, and the spec needs no change for them. Under §1s, however, GREEN cannot absorb them: RED r3 has to land before GREEN.

VERDICT: REJECTED
