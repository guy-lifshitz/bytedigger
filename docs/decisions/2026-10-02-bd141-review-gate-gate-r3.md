# Gate r3: bd#141 item 6 review-readiness gate (spec r3 + RED r3)

- Spec: `docs/decisions/2026-10-02-bd141-review-gate.md` (r3)
- RED: `engine_py/tests/test_bd141_p6_review_gate.py` (reported: 37 failed, 1 passed (R13))
- Prior: `…-gate-r1.md` (M1 + m1–m6), `…-gate-r2.md` (F1, F2 MAJOR; F3–F5 MINOR)
- Tier 2, round 3 of 3. Read-only audit; no tests or git were run. I re-audited the whole surface and simulated a spec-conforming GREEN against every test.

## r2 closure

| r2 | status | evidence |
|---|---|---|
| F1 (R11 cannot go green) | closed | test :288-294 now calls `readiness.verdict(rig.repo, "deploy")` directly inside `pytest.raises(ValueError)`. Pre-GREEN, the message is `stage must be 'start' or 'ship'`, so the run fails at `assert "review" in …`. Post-GREEN, the message names all three stages and the test passes. |
| F2 (R10 env hijack) | closed | test :270-273: both rig1 checks (`start` APPROVED, `review` label_absent) run before `rig2` is built at :277. After that point only rig2 is evaluated. |
| F3 (duplicate sentence) | closed | spec :57-58 has a single "choices become" statement. |
| F4 (`post` exit 4) | closed | the §4 bullet now names `readiness post` (spec :241-242). |
| F5 (edges) | closed | R1b (:137), R3b (:166) and R9b (:256) were added, with matching ACs at spec :134-136, :142-143 and :156-157. |

## Step 1: spec-internal consistency

These tokens are stable across the spec and RED: `_readiness_review_gate`, `readiness_review_verdict`, `E_READINESS_NOT_APPROVED`, `review_label`, `build_review_prompt`, `_readiness`, `ambient_git_cwd`, `start|review|ship`, `{start|review|ship}`, and the five event keys. I found no drift. The test count is 10 + 11 (R9 has 9 parametrized cases, plus R9-not-required and R9b) + 4 + 8 + 3 + 2 = 38. That matches the observed 37F/1P.

## Step 1.5: rule-overlap simulation (GREEN = `pol._replace(label=review_label)`, `spec_text=None`)

`_decide` (readiness.py:491-528) at stage `review`:
- R3: the record is valid at ts 200. The want-label is `ready-for-review`, so the event is LE_2 (400, alice) and its owner is None. The label is present, 400 > 200, and neither the approvers nor the distinct_actor rule applies. Result: APPROVED.
- R3b: the consumption names LE_1, but the owner is looked up by LE_2 and is None. Result: APPROVED.
- R4: there is no ready event and no ready label, so the result is `label_absent` (:518).
- R5: the event is at 400, which is <= the record at 500. Result: `label_predates_spec` (:520).
- R6: bd-bot added the event and distinct_actor is set. Result: `self_approved` (:524).
- R7: bob is not in `["alice"]`. Result: `approver_not_allowed` (:522).
- R8: `_bind` returns `no_issue`. Result: NOT_APPROVED with issue None.
- The ship-retry branch (:514) and spec_changed (:526) are stage-guarded, so they never fire at review.

Policy half (`_load_policy` :273-294 plus the op1 check before :292):
- R2: `required:false` with a valid label gives None, so OFF and no gh call (the policy read is git only).
- R9: `""`, `5` and `"Plan-Approved"` all raise `_Unavailable("…wrong type…", policy=True)` at every stage, including under `required:false`.
- R9b: `null` is read as None, so review is OFF and start is APPROVED.
- R1/R1b: GREEN must short-circuit to `_default_result()` before `_bind` sets `required`/`label` and binds the issue. The spec says so explicitly, and R1b catches the natural wrong order.

## Step 2: §2 against §3

- op1 maps to R1–R12. op2 maps to R14–R19. op3 maps to R13, R20 and R21. The op3 terminal path maps to R24. op4 maps to R22 and R23.
- The refusal/abort terminal path has a producing §2 path (op3 "Terminal path") and an AC (R24). `_on_phase_6_abort` (phase_6_review.py:5880-5915) mkdirs the parent and writes the stub when the doc is absent.
- No mismatch.

## Step 3: RED adequacy (per-test GREEN simulation)

| Test | Fails now at | Passes after a conforming GREEN |
|---|---|---|
| R1, R1b, R2, R3, R3b, R4–R8, R9-review, R9-not-required, R9b | `_verdict` helper `pytest.fail` (ValueError on `review`), raised in the test body | yes (see 1.5) |
| R9-start/ship (6) | `assert verdict == "UNAVAILABLE"`: start/ship ignore `review_label` today and return APPROVED | yes |
| R10 | helper `pytest.fail` at :273 | yes; rig1 is evaluated before rig2 |
| R11 | `assert "review" in str(ei.value)` | yes |
| R12 | `assert rc == 3` (argparse exit 2) | yes. Each rig is checked right after it is built. `_report` treats review like start, so the policy-read gives 0 at review and 4 at ship. |
| R13 | passes (pin) | yes |
| R14–R19 | `_gate()` / `_rd()` asserts | yes. `_emit_safe` and `resolve_git_cwd_with_source` are p6 module globals (:128, :150), so the monkeypatches bite. R19: no `git_cwd`, and the scratch climb finds no `.git` (the test asserts this), so the source is `"cwd"`, which `is_ambient_git_cwd` treats as ambient (git_cwd.py:40-44, 93-95). |
| R20 | `assert calls == []` (pre-GREEN the step reaches `_review_plan` at :748) | yes |
| R21 | `assert len(calls) == 1` after rig2 (pre-GREEN the recorder is hit twice) | yes. Rig1 is OFF (no `readiness` key) and is checked before rig2 is built. |
| R24 | `assert res is not None` | yes. `StepResult` has `step_name`, `error_code` and `recoverable` (contracts.py:75-82). |
| R22, R23 | `phase_6_review` / `review_label` / `{start|review|ship}` needles absent | yes once docs are regenerated or edited (`## E_READINESS` heading at ERROR_CODES.md:261; `## Readiness gate` at configuration.md:47; `## [Unreleased]` at CHANGELOG.md:13) |

- Cross-talk sweep: the multi-rig tests are R10, R12 and R21. In all three, every evaluation of a rig happens before the next `make_rig` repoints `HAL_GH_BIN`, `GIT_SSH_COMMAND` and `PATH`. All other tests build one rig. No residual F2-class hazard remains.
- §1l: no test mocks its own UUT. The spies sit on collaborators (`verdict`, `resolve_git_cwd_with_source`, `_review_plan`), and R20, R21 and R24 run the real step and the real handler.
- Stub-passability: a `return None` stub passes R14 only. R15–R21 and R24 redden it.

## Step 4: reachability (§1y)

| AC | Point | Host | Test path |
|---|---|---|---|
| R20/R21/R24 | `refusal = _readiness_review_gate(ctx, _prev)`, the first statement | `_build_review_prompt` (:733) | `StepContract("build_review_prompt")` → engine step loop. The tests call the step directly. |
| R24 | `atomic_write(doc, _render_not_assessed_stub(result))` (:5915) | `_on_phase_6_abort` (:5880) | engine error_handler. The test calls the handler directly. |
| R1–R12 | stage set (readiness.py:633), `_load_policy`, `_bind`, `_decide`, `_report` (:690), argparse choices (:712) | `verdict` / `main` | direct |

## Adversarial edges (not covered by any §3 AC)

1. **Ship or post with a valid `review_label` set.** `check --stage ship` should still consume the plan-label event and remove only `plan-approved`. `readiness post` should likewise remove only the plan label. Neither should ever touch `ready-for-review`. R10 pins `start` only. A GREEN that threads a review-relabelled `_Policy` through `_bind` would be caught at start (R10) only if the relabelling happened there.
2. **Custom `label` plus `review_label` equal to it under casefold.** For example, `label: "Go"` with `review_label: "go"`. R9 covers this only against the default `plan-approved`. A GREEN that compares against `LABEL_DEFAULT` instead of `label` would pass R9.

## Findings

**m1 — MINOR (non-discriminating needle).** The R23 needle `bd#141 item 6` already appears in `CHANGELOG.md` `[Unreleased]` (line 18, from the start gate, PR #160). So it is green before GREEN. The `review_label` needle in the same section is what reddens R23. No change is needed. Optionally, tighten it to `bd#141 item 6` together with `review` when the test is next touched.

**m2 — MINOR (advisory).** Edges 1 and 2 above are cheap guards. Neither is required by a spec line that the current RED leaves untested in a way that blocks a correct GREEN. They can be added in a follow-up and need no change now.

**m3 — MINOR (wording).** The test-file docstring (:5-6) calls the helper's `pytest.fail` "assert time". It is a deliberate in-body failure with a stated reason, not a collection error, so it satisfies §1q. This is cosmetic.

No BLOCKER or MAJOR findings. Every test can pass after a spec-conforming GREEN, and each one is red now at an in-body failure for the stated reason.

VERDICT: APPROVED
