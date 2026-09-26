# bd#82 PR2 — gates and reviews start fresh sessions; step context crosses thread pools

Issue: #82 (part 2 of 4). Class: SYSTEMATIC. Chokepoint: `llm_subprocess.invoke_llm_subprocess`
→ `_dispatch_backend`. Stacked on PR1 (#96). Ports HAL PR #1898 (step context in thread pools).

## Problem (verified against the code)

- **Warm resume.** agent-sdk keeps a per-process session cache keyed `run_id:<step_name before
  the first ".">` (`lib/reference_backends/agent_sdk.py:63-68`) and resumes it on the next
  call. Callers cannot ask for a fresh session.
  - Hard gates (validation, integrity, plan-review, satisfaction) resume the transcript of their
    previous call. That includes the phase 4.5 REVISE→repoll loop (`phase_45_spec.py:3854`).
  - Phase 4.5 and phase 6 both call `invoke_review_llm`, so the phase 6 review can resume the
    phase 4.5 spec-review session.
  - A judge that reads its own earlier verdict is not an independent judge.
- **Thread context.** `telemetry_ctx` keeps the step context in a `threading.local`.
  `_run_satisfaction_evaluators_parallel` (`phase_6_review.py:2770`) submits
  `invoke_llm_subprocess` to a `ThreadPoolExecutor` without that context. Each worker therefore
  sees `run_ctx=None`, which means:
  - no run telemetry and no attestation;
  - no tier;
  - `claude-in-session` fails with `E_LLM_RUN_ID_MISSING`.

  The three evaluators also share one step name.

## Contract

1. **`fresh_session`.** `invoke_llm_subprocess(..., fresh_session: bool = False)`. The effective
   value is `fresh_session or hard_gate`, so every hard gate is fresh.
   - It is passed only to backends that declare the new capability `warm_resume`. Other backends
     never receive a kwarg they did not declare, so third-party backends keep working.
   - The chokepoint resolves the value and forwards it on every dispatch: both `stable_prefix`
     branches and the GH1169 hang fallback. Production gates pass a `stable_prefix`, so both
     branches matter.
   - agent-sdk declares `warm_resume`. With a fresh session it neither reads nor writes the
     session cache, and it never invalidates it, even when the call fails. There is no resume,
     and the worker's own warm session survives.
2. **Non-gate judges are fresh.** Phase 6 `_invoke_review_llm` and the decorrelated verifier
   `_invoke_decorr_llm` pass `fresh_session=True`.
3. **`telemetry_ctx.run_with_current_run(prev, fn, /, *args, ctx_step_name=None, **kwargs)`**
   (port of HAL #1898).
   - The parent captures `get_current_run()` at submit time. The wrapper runs in the worker: it
     clears the slot, re-publishes `prev` through `set_current_run_from` (under `ctx_step_name`
     when given), runs `fn`, and always clears the slot in `finally`.
   - `prev is None` leaves the slot empty. `prev` and `fn` are positional-only.
   - `contextvars` was rejected: pool threads start with an empty context.
4. **The satisfaction pool uses the wrapper.** Worker *i* gets
   `step_name=invoke_satisfaction_llm_eval{i}`, with the index fixed at submit time.
   - Results come back in submit order.
   - No `ctx_step_name` is passed, so the telemetry slot keeps the parent's step name (as
     HAL ruled).
   - If every evaluator fails, the returned result is `results[0]`. Its step name is static
     either way: `..._eval0` for a dispatched call, or the parent's step name for a
     pre-dispatch refusal. So the retry-cap and rework keys in `engine.py` stay stable across
     cycles.
5. **Enforcement pin.** In `workflows/`:
   - every `.submit(` call passes `telemetry_ctx.run_with_current_run` first, with a
     non-literal `prev`;
   - no `Thread`, `Timer`, executor or pool `.map`, `run_in_executor` or `to_thread` call
     exists.

## Acceptance

Test file `engine_py/tests/test_bd82_fresh_sessions_thread_ctx.py`.

- Fresh sessions run the real agent-sdk backend through the chokepoint. Only the SDK `query` is
  replaced, and it records the `resume` each call asked for.
- Thread context runs real pool threads and the real `_run_satisfaction_evaluators_parallel`.
  Events land in a real `EventLog`.
- §1ab coverage:
  - fresh entry (cycle 1);
  - in-phase retry (`set_current_run_from`, cycle 2);
  - resume from a non-main thread.

| Clause | ACs |
|---|---|
| 1 | F1 (resume leg), F2, F2b, F3, F4, F4b, F5, F6b, F6d, F7, F8 |
| 2 | F6, F6c |
| 3 | T1, T2, T3, T3b, T4, T5, T7 |
| 4 | T6 |
| 5 | T9 |

## Sibling tests (must stay green)

`test_gh705_satisfaction_stable_prefix`, `test_phase_6_satisfaction_multi_evaluator`,
`test_ccbb65dc_straggler_watchdog`, `test_gh1157_agent_sdk_retry`,
`test_gh1169_agent_sdk_hang_recovery`, `test_bd71_agent_sdk_observations`,
`test_gh379_decorr_verify`, `test_subprocess_telemetry`, `test_gh497_telemetry_hygiene`.

## Out of scope (later PRs of #82)

semantic_verifier through the chokepoint (PR3); backend per role and effort as a capability (PR4).
