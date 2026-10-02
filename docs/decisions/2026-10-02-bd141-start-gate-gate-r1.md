# bd#141 item 6: start-gate validation gate, round 1 of 3 (TIER 2, Option-D)

Spec: `docs/decisions/2026-10-02-bd141-start-gate.md` (r1)
RED: `engine_py/tests/test_bd141_p6_start_gate.py`
Base: `97b84e8`, branch `bd141-p6-start-gate`

## Step 1: spec-internal consistency (literal-token drift)

- `readiness_start_verdict`, `E_READINESS_NOT_APPROVED`, `_readiness_start_gate`, `_readiness` alias, `SPEC_DOC_RELPATH` (`phase_5_implement.py:383`, `"specs/build-spec.md"`) and the five event keys are spelled the same way in §2, §3 and the RED.
- The error format in op1 matches phase 8 byte for byte (`phase_8_post_deploy.py:576,588`).
- Drift (MINOR): op3 says to remove "the 'only the ship gate in v1' statement". S14 and the doc use the real text, "get only the ship gate" (`docs/configuration.md:75`). The RED uses the real text, so this has no effect.
- Factual drift (part of MAJOR-1): op2 (spec:66-68) says two siblings drive `wf.steps[0].execute(ctx, None)` as the validation loop. Both citations are wrong. `test_phase_5_integration_canary.py:57-59` and `test_phase_5_canary_event_type_from_spec_36269734.py:129-131` drive `phase_5_integration_canary_workflow().steps[0]`, which is a different workflow. Neither one reaches `_validation_cycle_loop_execute`.
- Inaccuracy (MINOR): §4 (spec:141) says "Every Phase 5 entry with a **github** `origin`" does the policy read. `readiness.read_policy_blob` (`readiness.py:238-255`) runs `ls-remote` and the forced fetch for **any** origin (gitlab, local path, etc.). The github check (`_parse_github`) only runs later, inside `_bind`, after a required policy has loaded.

## Step 1.5: rule-overlap simulation (`readiness._decide`, `readiness.py:491-528`, plus `_bind` at 585-599)

| AC | rig | first matching branch | expected | OK |
|---|---|---|---|---|
| S2 | policy `{"other":1}` | `_load_policy`: `"readiness" not in data` returns None, so OFF (no gh call) | OFF, None, 0 events | yes |
| S3 | approve() + spec A | record valid, label on, event 300 > record 200, no approvers/distinct, spec equal: APPROVED, `retry=False`; `stage!="ship"` returns before `_consume` | APPROVED, no POST/DELETE | yes |
| S4 | record only, no label/event | `owner` None; `label_absent` (checked before `label_predates_spec`) | label_absent | yes |
| S5 | approve(A) + spec B | passes label/predates/approver/self checks; `spec_changed` at stage start | spec_changed | yes |
| S6 | approve(), no spec file | `spec_text=None`; spec check skipped; APPROVED | APPROVED | yes |
| S7 | branch `feature-x` | `_bind`: issue None gives `no_issue`; no gh call | no_issue, tag "" | yes |
| S8 | approve(), gh `exit1` | policy read is git-only and succeeds; `_read_issue` -> `gh api user` exits 1 -> `_Unavailable` | UNAVAILABLE | yes |
| S9 | verdict raises | helper's own catch | UNAVAILABLE, "internal error: RuntimeError('boom')" | yes (spec op1 requires the helper-level catch; `verdict` itself never raises because of `_run_guarded`) |

No overlap defects.

## Step 2: §2 vs §3 cross-check

| §2 path | AC |
|---|---|
| op1 OFF -> None, no event | S2 |
| op1 APPROVED -> None + event, never consumes | S3, S6 |
| op1 NOT_APPROVED -> error StepResult (all fields) + event | S4, S7 (helper), S11 (composite) |
| op1 spec path passed / absent | S5, S10, S6 |
| op1 UNAVAILABLE fail-open | S8 |
| op1 exception from `verdict` -> UNAVAILABLE | S9 |
| op1 exception from **resolving repo/spec** -> UNAVAILABLE | **no AC** (MINOR-3) |
| op2 first statement of composite, before LoopRunner | S11, S12 |
| op2 before the GH767 reroute sentinel block | **no AC** (MINOR-4) |
| op2 steps list unchanged | S1 |
| op2 re-entry idempotent | S3 (helper called twice) |
| op3 registries / docs / changelog | S13, S14 |

Every §3 terminal/error AC has a producing §2 path. Two §2 branches have no AC (both MINOR, listed below). Neither is a terminal-state post-condition, so neither is a REJECT trigger on its own.

## Step 3: RED adequacy

- **Collection:** `_p5()` is imported lazily, and `_readiness` / `_readiness_start_gate` are only accessed inside test bodies (§1q). Collection succeeds. S1 passes by design.
- **Assert-time reddening:**
  - S2-S10 fail with `AttributeError` on `p5._readiness_start_gate` / `p5._readiness`.
  - S12 fails with `AttributeError` at `monkeypatch.setattr(p5._readiness, ...)`.
  - S13 and S14 fail on missing text.
  - S11 reaches `_invoke_red_llm` (`phase_5_implement.py:1556`, which calls the module-global `invoke_llm_subprocess`, so the recorder does intercept it). The recorder raises `_Stop`, and S11 does not catch it. The pre-GREEN failure is therefore an uncaught exception, not the `calls == []` assertion (MINOR-5). The reason is still correct: the loop reached the LLM, so the gate is not wired.
- **Named reddening changes hold:**
  - S2 reddens if the helper emits for OFF.
  - S4/S7 redden on a wrong format or `recoverable=True`.
  - S5 reddens if the helper passes `None` instead of the spec (verdict APPROVED).
  - S6 reddens if the helper passes a missing path (`_read_spec_file` raises, giving UNAVAILABLE).
  - S8/S9 redden if the helper fails closed.
  - S10 reddens on wrong args or kwargs.
  - S11 reddens if the gate runs after LoopRunner.
  - S1 reddens if a StepContract is added.
- **Stub-passability / §1l:** No test mocks the UUT.
  - S9 and S10 patch the collaborator `readiness.verdict`. S10's spy calls through to the real function.
  - S11 runs the real composite against a real git remote (ssh shim) and the stateful fake gh. It replaces only `invoke_llm_subprocess` and observes real repo state (`status --porcelain`, HEAD).
  - Scratch lives at `tmp_path/scratch`, outside `tmp_path/repo`, so the porcelain assertion is sound.
- **Path fidelity (S10):** `_resolve_scratchpad` returns `.resolve()`d paths. pytest's basetemp is already resolved, so the literal `str(rig.root/...)` comparison holds on macOS too.

## Step 4: reachability (§1y)

- S4-S10: Point is the new helper body. Host is `phase_5_implement._readiness_start_gate`. The tests call it directly.
- S11/S12: Point is the first statement of `_validation_cycle_loop_execute` (`phase_5_implement.py:7875`). Host is that composite, which is `phase_5_implement_workflow().steps[0].execute` and is reached from `engine.execute`. The tests use that path.
- S13/S14: static artifacts.

The chain is complete.

## Adversarial edges (not covered by §3)

1. **Ambient git_cwd (decoy repo):** `org_config` has no `git_cwd`, and `scratchpad_dir` is outside any repo. `_resolve_git_cwd` falls back to `Path.cwd()` (`lib/git_cwd.py:93`). The gate then runs `ls-remote` plus a forced fetch into `refs/bd/policy` against whatever repo the process sits in. That is a ref write, which GH1220 (`lib/git_cwd.py:47-50`, "NEVER run a mutating git op there") forbids on an ambient cwd. It is also a readiness verdict on the wrong repo. A real sibling hits this path today (MAJOR-1).
2. **Concurrent re-entry across worktrees:** parallel Phase 5 entries in worktrees of one repo share `refs/bd/policy`. The forced fetches race for the ref lock. The loser sees `git fetch ... failed`, which becomes UNAVAILABLE and fails open, so the start gate is silently skipped under `required: true`. The ship gate still enforces, so this is advisory. It is not declared in §4.
3. **Scratchpad unset:** `_resolve_scratchpad` raises `ValueError` (`phase_workflows_common.py:140`). Per op1, the whole gate then becomes UNAVAILABLE and fails open, even though the record/label checks could still run with `spec_path=None`. Under a required policy with no label, the gate warns instead of refusing. (The loop fails later anyway, so impact is low.)
4. **Reroute ordering:** a GREEN that places the gate after the `phase_reroute` block (`phase_5_implement.py:7892-7911`) consumes the reroute marker and invalidates cycle-1 sentinels on a run it then refuses. No AC pins "before the reroute block" (MINOR-4).
5. **Cost on OFF repos:** every Phase 5 entry (including resumes) by every engine user with any origin now pays `ls-remote` plus a fetch, even with no `readiness` key. §4 declares this only for github origins. There is no §1b baseline for the added latency.

## Findings

1. **MAJOR-1: wrong §1a sibling audit, and the design ignores ambient git_cwd (GH1220).**
   - Spec:66-68 cites two siblings as drivers of the validation loop. Both drive `phase_5_integration_canary_workflow` (`test_phase_5_integration_canary.py:57-59`, `test_phase_5_canary_event_type_from_spec_36269734.py:129-131`), so the stated justification for "steps unchanged" rests on a false premise.
   - The tests that really drive the composite are missing from §5:
     - `test_GH706_validate_cap_directed_reject.py:330` calls `p5._validation_cycle_loop_execute(ctx, prev)` directly.
     - `test_gh1018_orphan_green_cycle_resolution.py:1362,2404` drives it via `engine.execute`.
     - `test_bd_orphan_green_real_loop_path.py:44` drives it via `engine.execute`.
     - `test_gh1626d_orphan_green_recovery.py` uses the same stage.
   - The gh1018, gh1626d and orphan-loop stages `git init` a repo with no remote (`:153`, `:146`), so they resolve to OFF with no network.
   - **GH706 ac7 is different.** Its ctx (`test_GH706_validate_cap_directed_reject.py:55-66,327`) has only `scratchpad_dir=tmp_path` and no `git_cwd`, so op1's `_resolve_git_cwd` falls back to `Path.cwd()`, which is the bytedigger checkout running pytest. After GREEN, that unit test will:
     - run a real `git ls-remote` plus a forced `fetch` into `refs/bd/policy` of the developer's or CI's checkout (a ref write);
     - be network-dependent, with up to 30 s git timeouts offline;
     - follow whatever `readiness` policy the bytedigger default branch carries.

     If that branch ever gains `readiness.required`, this or any similar test turns into a real `gh` read, or into a refusal that breaks ac7's `captured["args"]` assertion.
   - This contradicts the GH1220 invariant (`lib/git_cwd.py:47-50`) that `phase_5_implement` already observes elsewhere (`_resolve_git_cwd_with_source`, `phase_5_implement.py:2059-2064`).
   - **Required fix:**
     - op1 resolves the repo with `_resolve_git_cwd_with_source`. When `is_ambient_git_cwd(source)` is true, it makes no readiness call. Pick one outcome and pin it in the spec: return None with no event, or emit `readiness_start_verdict` UNAVAILABLE with reason "ambient git_cwd" and fail open.
     - Add an AC (ctx with `scratchpad_dir` only and `git_cwd` unset; spy shows `verdict` never called, or zero fake-gh calls and no `refs/bd/policy` in the cwd repo).
     - Correct op2's sibling citations, and list GH706, gh1018, gh1626d and the orphan-loop test in §5 Sibling tests.

2. **MINOR-2: §4 says "github origin", but the network read happens for any origin** (`readiness.py:238-255`). Reword §4 (and op3's doc text, if it repeats the claim).

3. **MINOR-3: op1's "exception from resolving repo/spec ⇒ UNAVAILABLE" has no AC.** Also consider degrading only the spec part (spec resolution failure gives `spec_path=None`, and evaluation continues) instead of failing open entirely (adversarial edge 3).

4. **MINOR-4: no AC pins the gate before the GH767 reroute block.** S11 proves the gate runs before LoopRunner only. A cheap pin: S11 with `org_config["phase_reroute"]` set and a run ctx, asserting no `phase_reroute_entry` event on refusal.

5. **MINOR-5: S11 (`test_bd141_p6_start_gate.py:276`) does not catch `_Stop`.** Pre-GREEN it fails with an uncaught exception instead of the `calls == []` assertion. Wrap it as S12 does (`:303-306`) so the RED fails at assert time (§1q).

6. **MINOR-6: undeclared fail-open under concurrency.** Parallel worktree entries race on the shared `refs/bd/policy`; the loser gets UNAVAILABLE and the gate is silently skipped (adversarial edge 2). Declare it in §4.

7. **MINOR-7: op3 paraphrase drift.** op3 says "only the ship gate in v1"; the doc and S14 use "get only the ship gate". Quote the real text in op3.

The design is otherwise sound:
- Placement as the first statement of the composite runs before any repo write and leaves the steps list unchanged.
- `start` never consumes, so re-entry is idempotent.
- `spec_changed` on a rerouted spec is a deliberate, documented hard stop.
- Fail-open on UNAVAILABLE matches the op-A3 layering, with the ship gate as the fail-closed enforcement layer.

VERDICT: REJECTED
