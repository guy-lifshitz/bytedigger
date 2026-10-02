# bd#141 item 6: start-gate validation gate, round 2 of 3 (TIER 2, Option-D)

Spec: `docs/decisions/2026-10-02-bd141-start-gate.md` (r2)
RED: `engine_py/tests/test_bd141_p6_start_gate.py` (r2)
Base: `97b84e8`, HEAD `081e98b`, branch `bd141-p6-start-gate`
Prior verdict: `2026-10-02-bd141-start-gate-gate-r1.md` (REJECTED: 1 MAJOR, 6 MINOR)

## r1 findings: resolution check

| r1 | status | evidence |
|---|---|---|
| MAJOR-1 ambient git_cwd + wrong siblings | resolved | op1 resolves via `_resolve_git_cwd_with_source` (`phase_5_implement.py:2059-2064`, already imports `is_ambient_git_cwd` at :155) and skips `verdict` when the source is ambient. It emits UNAVAILABLE `ambient_git_cwd` and returns None. S17 pins this. op2 no longer cites the canary tests. §5 lists GH706, gh1018, gh1626d and orphan-loop, and notes that the canary drives a different workflow. |
| MINOR-2 "github origin" | resolved | §4 now says "**any** `origin` and a non-ambient git_cwd". |
| MINOR-3 repo/spec resolution failure has no AC | resolved | Spec failure degrades to `spec_path=None` (S15). Repo failure fails open as UNAVAILABLE internal error (S16). |
| MINOR-4 reroute ordering has no AC | resolved | S18. |
| MINOR-5 S11 uncaught `_Stop` | resolved | `test_bd141_p6_start_gate.py:277-280` catches it, so pre-GREEN S11 fails at `assert calls == []`. |
| MINOR-6 concurrency undeclared | resolved | §4 "Concurrency" bullet. |
| MINOR-7 op3 paraphrase | resolved | op3 quotes `docs/configuration.md:75` verbatim. |

## Step 1: spec-internal consistency

- The literals `readiness_start_verdict`, `E_READINESS_NOT_APPROVED`, `_readiness_start_gate`, `_readiness`, `ambient_git_cwd`, `_resolve_git_cwd_with_source`, `_resolve_scratchpad`, `reroute_already_consumed`, `mark_reroute_consumed` and `phase_reroute_entry` are spelled the same in op1/op2, §3 and the RED. Each one matches an existing prod symbol (`phase_5_implement.py:155,168,171,2059,7896-7911`).
- `readiness.verdict(repo, stage, spec_path=None)` (`readiness.py:640`) matches op1's positional call and S10's `not kwargs` assertion.
- Drift (MINOR-1): the RED module docstring (`test_bd141_p6_start_gate.py:3-5`) still says "ACs S1..S14" and "test_S1_* .. test_S14_*". The spec and the file now contain S15-S18. This is cosmetic.

## Step 1.5: rule-overlap simulation

### Resolver (`lib/git_cwd.py:53-95`), first match wins

| AC | cfg / prev | first branch | source | ambient? | expected | OK |
|---|---|---|---|---|---|---|
| S2-S12, S15, S18 | `git_cwd=<abs rig.repo>` | level 1 | `cfg_git_cwd` | no | `verdict` called | yes |
| S16 | resolver patched to raise | (none) | (none) | (none) | catch-all: UNAVAILABLE internal error, `verdict` not called | yes |
| S17 | only `scratchpad_dir=<tmp>/scratch` (abs). The RED asserts no `.git` on the scratch path or any of its parents (:390). Process cwd is `rig.repo`. | levels 1-3 skip; level 4 climb finds nothing; level 5 | `cwd` | **yes** (`AMBIENT_GIT_CWD_SOURCES` :40-44) | skip, event `ambient_git_cwd` | yes |
| GH706 ac7 (`:327-330`) | `scratchpad_dir=tmp_path`, prev `data={"cycle":1}` with no `git_cwd` | level 5 (pytest basetemp has no `.git`) | `cwd` | yes | guard skips, so no fetch into the developer's checkout | yes |
| gh1018 / gh1626d / orphan-loop / pipeline_recovery | explicit `git_cwd` to a repo with no remote | level 1 | `cfg_git_cwd` | no | `verdict` returns OFF (no origin), no network | yes |

S17 really produces an ambient source, and the precondition at :390 makes the test fail loudly if tmp ever sits under a repo, instead of silently exercising `scratchpad_climb`. GH706 ac7 now skips the gate.

### Verdict mapping

The mapping is unchanged from r1, and the r1 table still holds for S2-S9. Additions:
- S15: spec failure degrades to `spec_path=None`. The record and label checks pass, the spec check is skipped, and the result is APPROVED.
- S18: the gate runs first and returns NOT_APPROVED `label_absent` before `_resolve_validation_cycle_cap` and the reroute block (`:7890-7911`).

No overlap defects.

## Step 2: §2 vs §3 cross-check

| §2 path | AC |
|---|---|
| op1 ambient source: skip, UNAVAILABLE event, None | S17 |
| op1 repo-resolution exception: UNAVAILABLE | S16 |
| op1 spec-resolution exception: `spec_path=None`, continue | S15 |
| op1 OFF / APPROVED / NOT_APPROVED / UNAVAILABLE / verdict crash | S2, S3+S6, S4+S5+S7, S8, S9 |
| op1 arguments | S10 |
| op2 before the reroute block | S18 |
| op2 before LoopRunner, composite refusal is a terminal error | S11 |
| op2 pass-through | S12 |
| op2 steps list unchanged | S1 |
| op3 docs and registries | S13, S14 |

Every §2 branch has an AC, and every terminal/error AC has a producing §2 path. No mismatch.

## Step 3: RED adequacy

**Collection:** `p5` symbols are accessed only inside test bodies, so collection succeeds. S1 passes by design.

**Pre-GREEN failures:**
- S2-S10, S12, S15, S16, S17 fail with `AttributeError` on `_readiness_start_gate` or `_readiness`. This is the same accepted test-body failure as in r1. The reason is the symbol that GREEN must create.
- S11 runs the real composite. The LLM recorder is called, and the test fails at `assert calls == []`.
- In S18, `reroute_already_consumed` returns False and `mark_reroute_consumed` returns True, so pre-GREEN the reroute block runs. Any crash in the real `invalidate_cycle_sentinels` is caught at :440. The test fails at `assert consumed_q == []`. It fails at assert time for the right reason: the gate is not placed before the reroute block.
- S13 and S14 fail on missing text.

These failures match the orchestrator's run (17 fail, S1 passes).

**Named reddening changes hold post-GREEN:**
- S17 without the guard: the spy appends to `calls` and then raises `AssertionError`. The helper's catch-all turns that into UNAVAILABLE and returns None, so `assert calls == []` reddens. The guard is load-bearing.
- S16, repo failure propagating: the exception escapes and the test errors.
- S15, spec failure mapped to UNAVAILABLE: the helper never calls `verdict`, so `len(calls) == 1` reddens. The event check also reddens.
- S18, gate after the reroute block: `consumed_q` becomes non-empty.

**Stub-passability / §1l:**
- No test patches the UUT `_readiness_start_gate`.
- These are patched collaborators: `readiness.verdict` (spies call through, except S9/S16/S17, which need a non-real behaviour by design), `_resolve_scratchpad`, `_resolve_git_cwd_with_source`, the reroute primitives, `telemetry_ctx.get_current_run`, `invoke_llm_subprocess` and `_emit_safe`.
- S11 and S17 observe real repo state: `status --porcelain`, HEAD, and `show-ref refs/bd/policy`.

**Fidelity of S15-S18 to the spec text:**
- All four match the spec. S15 accepts the spec arg either positionally or as a kwarg, which is lenient but consistent with S10's strict positional pin.
- S18's patched `get_current_run` returns an object with `run_id` only. That is enough for the composite (it reads only `run_ctx.run_id` at `:7896`).

## Step 4: reachability (§1y)

- S2-S10 and S15-S17:
  - Point: the helper body.
  - Host: `phase_5_implement._readiness_start_gate`.
  - Test path: the tests call it directly.
- S11, S12 and S18:
  - Point: the first statement of `_validation_cycle_loop_execute` (`:7875`).
  - Host: that composite, which is `phase_5_implement_workflow().steps[0].execute`.
  - Test path: the tests drive `steps[0].execute`.
- S13 and S14: static artifacts.

The chain is complete.

## Sibling list (§1a)

- I grepped `engine_py/tests` for `_validation_cycle_loop_execute|phase_reroute|steps[0]` and for `phase_5_implement_workflow(`. Every test that really reaches the composite is in §5: GH706, gh1018, orphan-loop, gh1626d and pipeline_recovery.
- `test_bd8_l1_oracle.py` runs `run.py --workflow phase_5_implement`, but under a fixture registrar (`:313-329`), so it never reaches the real composite.
- `test_bd18_emissions.py` uses synthetic workflows. Listing it is harmless.
- The other `phase_5_implement_workflow(` users index later steps (`green_watchdog`, `verify_green`, `commit_green_code`, typecheck). Their steps list is unchanged, and S1 guards that.

The list is complete.

## Adversarial edges (not covered by §3)

1. **Basetemp or scratchpad inside a real checkout.** Example: `pytest --basetemp=<bytedigger>/.tmp`, or a production scratchpad nested in the target repo. Here, GH706-style ctxs (scratchpad only) resolve via `scratchpad_climb`, which counts as explicit and not ambient. The gate then runs `ls-remote` plus a forced fetch into `refs/bd/policy` of the enclosing checkout. That is correct for production (the climb is the intended repo), but in tests it reintroduces the r1 side effect on the developer's checkout. Advisory; S17's precondition already protects S17 itself.
2. **Relative explicit git_cwd** (`git_cwd="."`, giving `cfg_git_cwd_relative`). This is ambient by design (`git_cwd.py:40-44`), so the start gate is silently skipped as UNAVAILABLE for a user who did configure a repo. The behaviour is correct under GH1220, but it is not pinned (S17 only exercises `cwd`). Advisory.
3. **Ambient event payload shape.** op1 says the ambient event carries the five keys with `issue`/`record_sha256` = None. S17 checks only `verdict` and `reason`, so a GREEN that emits a 2-key payload passes. Advisory (MINOR-2).

## Findings

1. MINOR-1: the RED module docstring is stale ("S1..S14", `test_bd141_p6_start_gate.py:3-5`); the file now has S15-S18. This is cosmetic, but tests are read-only for GREEN (§1s), so fix it in a RED touch-up commit or leave it.
2. MINOR-2: S17 does not assert `set(payload) == EVENT_KEYS` or `issue is None` for the ambient event (adversarial edge 3). This is advisory. Adding one line would bring S17 to the same strength as S3.
3. MINOR-3: no test pins the relative-source ambient path, or the climb-into-enclosing-checkout case for test ctxs (adversarial edges 1-2). This is advisory, with no spec change required.

There are no MAJOR findings. All r1 findings are resolved. The fixes introduced no new blocker: the ambient guard is consistent with `lib/git_cwd.py`, S17 really yields `source="cwd"`, GH706 ac7 now skips, and S15-S18 are faithful and fail at assert time for the right reason.

VERDICT: APPROVED
